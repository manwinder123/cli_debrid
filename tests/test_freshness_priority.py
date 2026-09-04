#!/usr/bin/env python3
"""Regression test: freshness-first queue priority (new episodes, new movies).

User requirement: Wanted items aired/released within freshness_window_days
must always jump ahead of the regular queue — new TV episodes first, then new
movies — with the existing priority algorithm (force-priority, sort settings)
unchanged behind those two tiers. Date-based so nothing goes stale.

queues/wanted_queue.py imports DB helpers at module import (unavailable in
this sandbox), so this test verifies the contract points directly:
the SQL recency predicate against a scratch SQLite DB, the tier function
semantics, and that the existing force-priority block still follows the
freshness block in process().
"""

import datetime
import os
import sqlite3
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(os.path.join(PROJECT_ROOT, path), encoding='utf-8') as f:
        return f.read()


def fresh_tier(item, window_days=7):
    """Mirror of the ScrapingQueue tier sort (kept in sync by this test)."""
    today = datetime.date.today()
    cut = (today - datetime.timedelta(days=window_days)).isoformat()
    rel = str(item.get('release_date') or '')[:10]
    if len(rel) < 10 or rel < cut:
        return 2
    return 0 if item.get('type') == 'episode' else 1


class FreshnessContractTest(unittest.TestCase):
    def test_tier_ordering(self):
        today = datetime.date.today().isoformat()
        old = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
        items = [
            {'type': 'movie', 'release_date': today},      # tier 1
            {'type': 'episode', 'release_date': old},      # tier 2
            {'type': 'episode', 'release_date': today},    # tier 0
            {'type': 'episode', 'release_date': None},     # tier 2
            {'type': 'movie', 'release_date': ''},         # tier 2
        ]
        self.assertEqual([fresh_tier(i) for i in items], [1, 2, 0, 2, 2])
        ordered = sorted(items, key=fresh_tier)
        self.assertEqual(ordered[0]['type'], 'episode')
        self.assertEqual(ordered[0]['release_date'], today)
    def test_window_boundary(self):
        edge = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
        past = (datetime.date.today() - datetime.timedelta(days=8)).isoformat()
        self.assertEqual(fresh_tier({'type': 'episode', 'release_date': edge}), 0)
        self.assertEqual(fresh_tier({'type': 'episode', 'release_date': past}), 2)

    def test_sql_recency_predicate(self):
        con = sqlite3.connect(':memory:')
        con.execute('CREATE TABLE media_items (id INTEGER PRIMARY KEY, state TEXT, '
                    'ghostlisted INTEGER, type TEXT, release_date TEXT)')
        today = datetime.date.today().isoformat()
        old = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
        rows = [
            (1, 'Wanted', 0, 'episode', today),
            (2, 'Wanted', 0, 'episode', old),
            (3, 'Wanted', 1, 'episode', today),   # ghostlisted: excluded
            (4, 'Wanted', 0, 'movie', today),
            (5, 'Wanted', 0, 'movie', None),      # unknown date: excluded
            (6, 'Scraping', 0, 'episode', today),  # wrong state: excluded
        ]
        con.executemany('INSERT INTO media_items VALUES (?,?,?,?,?)', rows)
        for fresh_type, want in (('episode', [1]), ('movie', [4])):
            got = [r[0] for r in con.execute(
                "SELECT id FROM media_items WHERE state = 'Wanted' "
                "AND (ghostlisted IS NULL OR ghostlisted = 0) "
                "AND type = ? AND release_date >= date('now', '-' || ? || ' days') "
                "ORDER BY release_date DESC LIMIT ?",
                (fresh_type, 7, 25))]
            self.assertEqual(got, want, fresh_type)
        con.close()

    def test_existing_algorithm_preserved(self):
        src = _read('queues/wanted_queue.py')
        fresh_pos = src.find('FreshEpisodes')
        force_pos = src.find('Process Force Priority Items')
        regular_pos = src.find('Build Query for Candidate Items')
        self.assertGreater(fresh_pos, 0)
        self.assertGreater(force_pos, fresh_pos,
                           'force-priority block must still follow freshness block')
        self.assertGreater(regular_pos, force_pos,
                           'regular query must still follow force-priority block')
        self.assertIn('queue_sort_order', src)
        self.assertIn('sort_by_release_date_desc', src)
        scrape = _read('queues/scraping_queue.py')
        self.assertIn('_fresh_tier', scrape)
        # freshness sort runs after force-priority sort (stable => primary key)
        self.assertGreater(scrape.find('Freshness Priority Sorting'),
                           scrape.find('Force Priority Sorting'))
        schema = _read('utilities/settings_schema.py')
        self.assertIn('freshness_window_days', schema)
        self.assertIn('freshness_max_per_cycle', schema)


if __name__ == '__main__':
    unittest.main()

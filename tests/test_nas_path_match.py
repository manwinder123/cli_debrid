import os
import sys
import tempfile
import unittest
from unittest import mock

# Add the project root to the path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# database/__init__.py eagerly imports every database + routes submodule, and
# those write their working files under the USER_* env vars (falling back to
# /user/*, which is read-only in this sandbox). Point them at a writable temp
# dir so the import succeeds — the same mechanism the real deployment uses.
_TMP_ENV = tempfile.mkdtemp(prefix='cli_debrid_tests_')
os.environ.setdefault('USER_DIR', _TMP_ENV)
os.environ.setdefault('USER_CONFIG', os.path.join(_TMP_ENV, 'config'))
os.environ.setdefault('USER_DB_CONTENT', os.path.join(_TMP_ENV, 'db_content'))
os.environ.setdefault('USER_LOGS', os.path.join(_TMP_ENV, 'logs'))
for _d in (os.environ['USER_CONFIG'], os.environ['USER_DB_CONTENT'],
           os.environ['USER_LOGS']):
    os.makedirs(_d, exist_ok=True)

# The eager database/scraper package imports pull in the whole app (routes,
# debrid providers, cli_battery) and one of those routes back into
# utilities.local_library_scan mid-import — a circular import. Stub the heavy
# packages/submodules so only the modules local_library_scan actually needs
# (core, database_reading, database_writing, symlink_verification) load for
# real.
sys.modules.setdefault('routes', mock.MagicMock())
sys.modules.setdefault('routes.api_tracker', mock.MagicMock())
sys.modules.setdefault('routes.poster_cache', mock.MagicMock())
for _sub in ('collected_items', 'blacklist', 'schema_management',
             'poster_management', 'statistics', 'wanted_items',
             'maintenance', 'not_wanted_magnets'):
    sys.modules.setdefault(f'database.{_sub}', mock.MagicMock())

from utilities.local_library_scan import (
    get_nas_file_index,
    check_local_file_in_nas_paths,
)


class TestNasPathMatch(unittest.TestCase):
    """Tests for NAS / Network Drive local-copy matching.

    These build a throwaway tree under a temp dir and match items against an
    explicit index (bypassing settings + the module-level index cache), so the
    tests are hermetic and don't touch any real NAS mount.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _index(self):
        return get_nas_file_index([self.root], force_refresh=True)

    def _mk(self, relpath, content=b'x'):
        full = os.path.join(self.root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as f:
            f.write(content)
        return full

    # ── Movies ────────────────────────────────────────────────────────────
    def test_movie_matches_parenthesized_year(self):
        """A real Plex-style name 'The Mummy (1999).mkv' must match title+year."""
        self._mk('Movies/The Mummy (1999)/The Mummy (1999).mkv')
        idx = self._index()
        res = check_local_file_in_nas_paths(
            {'title': 'The Mummy', 'year': 1999, 'type': 'movie'}, nas_index=idx)
        self.assertIsNotNone(res)
        self.assertTrue(res['path'].endswith('The Mummy (1999).mkv'))

    def test_movie_rejects_different_year(self):
        """A sequel with a different year must NOT satisfy a movie's check."""
        self._mk('Movies/The Mummy Returns (2001)/The Mummy Returns (2001).mkv')
        idx = self._index()
        res = check_local_file_in_nas_paths(
            {'title': 'The Mummy', 'year': 1999, 'type': 'movie'}, nas_index=idx)
        self.assertIsNone(res)

    def test_movie_prefers_matching_year_over_sequel(self):
        """When both exist, the same-title matching-year file wins."""
        self._mk('Movies/A/The Mummy Returns (2001).mkv')
        self._mk('Movies/B/The Mummy (1999).mkv')
        idx = self._index()
        res = check_local_file_in_nas_paths(
            {'title': 'The Mummy', 'year': 1999, 'type': 'movie'}, nas_index=idx)
        self.assertIsNotNone(res)
        self.assertTrue(res['path'].endswith('The Mummy (1999).mkv'))

    def test_movie_no_year_accepts_undated_file(self):
        self._mk('Movies/The Mummy/The Mummy.mkv')
        idx = self._index()
        res = check_local_file_in_nas_paths(
            {'title': 'The Mummy', 'year': None, 'type': 'movie'}, nas_index=idx)
        self.assertIsNotNone(res)

    def test_movie_no_year_rejects_sequel_with_year(self):
        """Ambiguous title match against a differently-yeared file is refused."""
        self._mk('Movies/The Mummy Returns (2001).mkv')
        idx = self._index()
        res = check_local_file_in_nas_paths(
            {'title': 'The Mummy', 'year': None, 'type': 'movie'}, nas_index=idx)
        self.assertIsNone(res)

    # ── TV episodes ───────────────────────────────────────────────────────
    def test_episode_matches_exact_se(self):
        self._mk('TV/Breaking Bad/Breaking Bad S01E02.mkv')
        idx = self._index()
        res = check_local_file_in_nas_paths(
            {'title': 'Breaking Bad', 'type': 'episode',
             'season_number': 1, 'episode_number': 2}, nas_index=idx)
        self.assertIsNotNone(res)
        self.assertTrue(res['path'].endswith('Breaking Bad S01E02.mkv'))

    def test_episode_does_not_match_wrong_episode(self):
        """One episode of a show must NOT validate a different episode."""
        self._mk('TV/Breaking Bad/Breaking Bad S01E01.mkv')
        idx = self._index()
        res = check_local_file_in_nas_paths(
            {'title': 'Breaking Bad', 'type': 'episode',
             'season_number': 1, 'episode_number': 2}, nas_index=idx)
        self.assertIsNone(res)

    def test_episode_does_not_match_other_season(self):
        self._mk('TV/Show/Show S02E02.mkv')
        idx = self._index()
        res = check_local_file_in_nas_paths(
            {'title': 'Show', 'type': 'episode',
             'season_number': 1, 'episode_number': 2}, nas_index=idx)
        self.assertIsNone(res)

    def test_episode_matches_spelled_out_form(self):
        self._mk('TV/Show/Show - Season 1 Episode 2.mkv')
        idx = self._index()
        res = check_local_file_in_nas_paths(
            {'title': 'Show', 'type': 'episode',
             'season_number': 1, 'episode_number': 2}, nas_index=idx)
        self.assertIsNotNone(res)

    # ── Season packs ──────────────────────────────────────────────────────
    def test_season_pack_matches(self):
        self._mk('TV/Show/Show S01.mkv')
        idx = self._index()
        res = check_local_file_in_nas_paths(
            {'title': 'Show', 'type': 'episode', 'season_number': 1}, nas_index=idx)
        self.assertIsNotNone(res)

    def test_season_pack_rejects_wrong_season(self):
        self._mk('TV/Show/Show S02.mkv')
        idx = self._index()
        res = check_local_file_in_nas_paths(
            {'title': 'Show', 'type': 'episode', 'season_number': 1}, nas_index=idx)
        self.assertIsNone(res)

    # ── Exact original-filename match ─────────────────────────────────────
    def test_exact_original_filename_wins(self):
        """filled_by_file basename is the strongest signal, even vs. title."""
        self._mk('Other/Totally.Different.1998.mkv')
        idx = self._index()
        res = check_local_file_in_nas_paths(
            {'title': 'Random Title', 'year': 2020, 'type': 'movie',
             'filled_by_file': 'Totally.Different.1998.mkv'}, nas_index=idx)
        self.assertIsNotNone(res)
        self.assertTrue(res['path'].endswith('Totally.Different.1998.mkv'))

    # ── No match / index shape ────────────────────────────────────────────
    def test_no_match_returns_none(self):
        self._mk('Movies/Something Else (2001).mkv')
        idx = self._index()
        res = check_local_file_in_nas_paths(
            {'title': 'The Mummy', 'year': 1999, 'type': 'movie'}, nas_index=idx)
        self.assertIsNone(res)

    def test_index_normalizes_names(self):
        self._mk('Movies/The Mummy (1999)/The Mummy (1999).mkv')
        idx = get_nas_file_index([self.root], force_refresh=True)
        self.assertEqual(len(idx), 1)
        self.assertEqual(idx[0]['norm'], 'the mummy 1999')  # name without extension

    def test_empty_item_title_returns_none(self):
        idx = self._index()
        res = check_local_file_in_nas_paths({'title': '', 'type': 'movie'}, nas_index=idx)
        self.assertIsNone(res)


if __name__ == '__main__':
    unittest.main()

import datetime as _dt
import os
import sys
import unittest
from unittest.mock import patch

# Add the project root to the path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# nas_source_adoption keeps its module-level imports light (settings + media_category)
from utilities.nas_source_adoption import _should_run_today, _verify_nas_in_plex


class TestNasSourceAdoption(unittest.TestCase):
    """Unit tests for the NAS Source Adoption job's pure logic."""

    # ── Daily latch ─────────────────────────────────────────────────────
    def test_should_run_when_past_run_time_and_not_run_today(self):
        with patch('utilities.nas_source_adoption.datetime') as mdt:
            mdt.now.return_value = _dt.datetime(2026, 7, 31, 4, 0, 0)  # 04:00
            mdt.strptime = _dt.datetime.strptime
            self.assertTrue(_should_run_today('03:00', '2026-07-30'))

    def test_should_not_run_before_run_time(self):
        with patch('utilities.nas_source_adoption.datetime') as mdt:
            mdt.now.return_value = _dt.datetime(2026, 7, 31, 2, 0, 0)  # 02:00
            mdt.strptime = _dt.datetime.strptime
            self.assertFalse(_should_run_today('03:00', '2026-07-30'))

    def test_should_not_run_twice_same_day(self):
        with patch('utilities.nas_source_adoption.datetime') as mdt:
            mdt.now.return_value = _dt.datetime(2026, 7, 31, 4, 0, 0)
            mdt.strptime = _dt.datetime.strptime
            self.assertFalse(_should_run_today('03:00', '2026-07-31'))

    def test_invalid_run_time_falls_back(self):
        # Invalid HH:MM defaults to 03:00; at 04:00 that means "should run".
        with patch('utilities.nas_source_adoption.datetime') as mdt:
            mdt.now.return_value = _dt.datetime(2026, 7, 31, 4, 0, 0)
            mdt.strptime = _dt.datetime.strptime
            self.assertTrue(_should_run_today('not-a-time', '2026-07-30'))

    # ── Plex verification ───────────────────────────────────────────────
    NAS = ('/mnt/das_pool/', '/mnt/das_pool2/')

    def test_exact_full_path_match(self):
        idx = {'movie.mkv': {'/mnt/das_pool/Movies/Movie/Movie.mkv'}}
        self.assertTrue(_verify_nas_in_plex('/mnt/das_pool/Movies/Movie/Movie.mkv', idx, self.NAS))

    def test_basename_match_under_nas_prefix(self):
        # Plex reports the NAS file under a different sub-path of the same NAS
        # mount (path-mapping tolerance); index paths are lowercased by the fetch
        idx = {'movie.mkv': {'/mnt/das_pool/Movies/Movie (1999)/movie.mkv'}}
        self.assertTrue(_verify_nas_in_plex('/mnt/das_pool/Movies/Movie/Movie.mkv', idx, self.NAS))

    def test_basename_match_not_under_nas_prefix_rejected(self):
        # Only the symlink entry carries the basename -> NOT verified
        idx = {'movie.mkv': {'/mnt/symlinked/Movies/Movie/Movie.mkv'}}
        self.assertFalse(_verify_nas_in_plex('/mnt/das_pool/Movies/Movie/Movie.mkv', idx, self.NAS))

    def test_no_basename_match(self):
        self.assertFalse(_verify_nas_in_plex('/mnt/das_pool/Movies/Movie/Movie.mkv', {}, self.NAS))

    def test_plex_unreachable_returns_false(self):
        self.assertFalse(_verify_nas_in_plex('/mnt/das_pool/Movies/Movie/Movie.mkv', None, self.NAS))

    def test_case_insensitive_match(self):
        # Index paths are lowercased by _fetch_plex_basename_paths; the check is
        # case-insensitive on both sides.
        idx = {'movie.mkv': {'/mnt/das_pool/movies/movie/movie.mkv'}}
        self.assertTrue(_verify_nas_in_plex('/mnt/das_pool/Movies/Movie/Movie.mkv', idx, self.NAS))


if __name__ == '__main__':
    unittest.main()

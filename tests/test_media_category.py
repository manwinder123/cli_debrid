import os
import sys
import unittest

# Add the project root to the path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utilities.media_category import derive_is_anime, derive_is_documentary, media_category


class TestMediaCategory(unittest.TestCase):
    """Unit tests for the NAS Source Adoption category classification."""

    def test_movie_non_anime(self):
        self.assertEqual(media_category({'type': 'movie', 'genres': ['Drama']}), 'movies')

    def test_episode_non_anime(self):
        self.assertEqual(media_category({'type': 'episode', 'genres': ['Comedy']}), 'tv_shows')

    def test_movie_anime_by_genre(self):
        self.assertEqual(media_category({'type': 'movie', 'genres': ['Animation', 'Anime']}), 'anime_movies')

    def test_episode_anime_by_genre(self):
        self.assertEqual(media_category({'type': 'episode', 'genres': ['anime']}), 'anime_tv')

    def test_anime_flag_precedence(self):
        # trigger_is_anime wins even if genres say non-anime
        self.assertEqual(
            media_category({'type': 'movie', 'trigger_is_anime': True, 'genres': ['Drama']}),
            'anime_movies')

    def test_anime_flag_false_does_not_win(self):
        self.assertEqual(
            media_category({'type': 'episode', 'trigger_is_anime': False, 'genres': ['Anime']}),
            'anime_tv')

    def test_genres_json_string(self):
        # DB can store genres as a JSON string
        self.assertEqual(
            media_category({'type': 'movie', 'genres': '["Adventure", "Anime"]'}),
            'anime_movies')

    def test_genres_invalid_json_string(self):
        # Invalid JSON falls back to treating the raw string as one genre
        self.assertEqual(media_category({'type': 'movie', 'genres': 'garbage'}), 'movies')

    def test_missing_type_defaults_to_tv(self):
        # No type / unknown type is treated as TV (episode) side
        self.assertEqual(media_category({'genres': ['Drama']}), 'tv_shows')

    def test_derive_is_anime_flag_variants(self):
        self.assertTrue(derive_is_anime({'trigger_is_anime': 1}))
        self.assertTrue(derive_is_anime({'trigger_is_anime': 'true'}))
        self.assertTrue(derive_is_anime({'trigger_is_anime': '1'}))
        self.assertFalse(derive_is_anime({'trigger_is_anime': 0}))
        self.assertFalse(derive_is_anime({'trigger_is_anime': 'false'}))

    # ── Documentaries ───────────────────────────────────────────────────
    def test_movie_documentary(self):
        self.assertEqual(media_category({'type': 'movie', 'genres': ['Documentary']}), 'documentary_movies')

    def test_episode_documentary(self):
        self.assertEqual(media_category({'type': 'episode', 'genres': ['History', 'Documentary']}), 'documentary_tv')

    def test_documentary_json_string(self):
        self.assertEqual(
            media_category({'type': 'movie', 'genres': '["Documentary", "Crime"]'}),
            'documentary_movies')

    def test_documentary_genre_variants(self):
        self.assertEqual(media_category({'type': 'episode', 'genres': ['docu-drama', 'documentary series']}), 'documentary_tv')
        self.assertTrue(derive_is_documentary({'genres': ['documentary']}))
        self.assertFalse(derive_is_documentary({'genres': ['Drama']}))

    def test_anime_precedence_over_documentary(self):
        # Anime flag wins when both signals are present
        self.assertEqual(
            media_category({'type': 'movie', 'genres': ['Anime', 'Documentary']}),
            'anime_movies')


if __name__ == '__main__':
    unittest.main()

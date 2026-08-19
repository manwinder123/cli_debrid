"""Pure helpers for classifying media items by category.

Used by the NAS Source Adoption job to apply per-category toggles (movies /
tv shows / anime movies / anime tv / documentary movies / documentary tv).
Kept free of heavy imports so it can be unit-tested in isolation.
"""
import json
import re
from typing import Any, Dict


def genres_contain_anime(genres) -> bool:
    """Word-boundary check: is 'anime' one of the genre words?

    Must NOT substring-match 'Animation' — kids cartoons carry that genre and
    were being misclassified as anime everywhere (relaxed matching, title
    guard bypass, anime filter thresholds), which let unrelated releases
    (e.g. 'Hit.Point.S01E04' for 'Angela Anaconda S01E04') through.
    Accepts a list or a single string of genres.
    """
    if isinstance(genres, str):
        genres = [genres]
    if not genres:
        return False
    for g in genres:
        if re.search(r'(?<![a-z0-9])anime(?![a-z0-9])', (g or '').lower()):
            return True
    return False


def _genres(item: Dict[str, Any]) -> list:
    """Genres as a list — the DB can store them as a JSON string."""
    genres_raw = item.get('genres') or item.get('trigger_genres') or []
    if isinstance(genres_raw, str):
        try:
            genres_raw = json.loads(genres_raw)
        except Exception:
            genres_raw = [genres_raw]
    if isinstance(genres_raw, list):
        return [str(g or '').lower() for g in genres_raw]
    return []


def derive_is_anime(item: Dict[str, Any]) -> bool:
    """Whether an item is anime.

    Prefers the `trigger_is_anime` DB flag, falling back to the anime genre.
    Mirrors queues/torrent_processor.py. There is no separate folder-setting
    dependency — this is purely per-item metadata.
    """
    flag = item.get('trigger_is_anime')
    if isinstance(flag, str):
        flag = flag.strip().lower() in ('1', 'true', 'yes')
    if bool(flag):
        return True
    return genres_contain_anime(_genres(item))


def derive_is_documentary(item: Dict[str, Any]) -> bool:
    """Whether an item is a documentary — genre-based (no DB flag exists)."""
    return any('documentary' in g for g in _genres(item))


def media_category(item: Dict[str, Any]) -> str:
    """Return one of 'movies' | 'tv_shows' | 'anime_movies' | 'anime_tv'
    | 'documentary_movies' | 'documentary_tv'."""
    is_movie = str(item.get('type') or '').lower() in ('movie', 'film')
    if derive_is_anime(item):
        return 'anime_movies' if is_movie else 'anime_tv'
    if derive_is_documentary(item):
        return 'documentary_movies' if is_movie else 'documentary_tv'
    return 'movies' if is_movie else 'tv_shows'

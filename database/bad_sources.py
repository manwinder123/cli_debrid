import json
import os
import logging
from typing import Dict

# Registry of debrid sources (torrent infohashes) that are known to be unservable
# (e.g. Real-Debrid returns no unrestricted link / empty_link, or the torrent was
# removed from RD). When a source is registered here, cli_debrid's matcher skips it
# and re-selects a different, servable source for the same item.
#
# This mirrors database/manual_blacklist.py's persistence pattern.

DB_CONTENT_DIR = os.environ.get('USER_DB_CONTENT', '/user/db_content')
BAD_SOURCES_FILE = os.path.join(DB_CONTENT_DIR, 'bad_sources.json')

# Cache so we don't hit disk on every lookup.
_cached_bad = None
_cached_mtime = None


def _get_cached() -> Dict[str, str]:
    global _cached_bad, _cached_mtime
    try:
        current_mtime = os.path.getmtime(BAD_SOURCES_FILE) if os.path.exists(BAD_SOURCES_FILE) else None
    except Exception:
        current_mtime = None
    if _cached_bad is None or current_mtime != _cached_mtime:
        _cached_bad = load_bad_sources()
        _cached_mtime = current_mtime
    return _cached_bad


def load_bad_sources() -> Dict[str, str]:
    """Return {infohash_lower: reason}. Empty dict if file absent/unreadable."""
    try:
        os.makedirs(os.path.dirname(BAD_SOURCES_FILE), exist_ok=True)
        if not os.path.exists(BAD_SOURCES_FILE):
            return {}
        with open(BAD_SOURCES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logging.warning(f"Could not load bad_sources.json: {e}")
        return {}


def _save(data: Dict[str, str]) -> None:
    global _cached_bad, _cached_mtime
    try:
        os.makedirs(os.path.dirname(BAD_SOURCES_FILE), exist_ok=True)
        tmp = BAD_SOURCES_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, BAD_SOURCES_FILE)
    except Exception as e:
        logging.error(f"Could not save bad_sources.json: {e}")
        return
    _cached_bad = data
    _cached_mtime = os.path.getmtime(BAD_SOURCES_FILE) if os.path.exists(BAD_SOURCES_FILE) else None


def is_bad_source(infohash: str) -> bool:
    """True if the (lowercased) infohash is a known-unservable source."""
    if not infohash:
        return False
    h = str(infohash).strip().lower()
    return h in _get_cached()


def mark_bad_source(infohash: str, reason: str = 'unservable') -> None:
    """Register an infohash as unservable so it is skipped during re-selection."""
    if not infohash:
        return
    h = str(infohash).strip().lower()
    data = _get_cached()
    data[h] = reason
    _save(data)
    logging.info(f"Bad-source registered: {h} ({reason})")


def get_bad_sources() -> Dict[str, str]:
    return dict(_get_cached())


def clear_bad_source(infohash: str) -> bool:
    h = str(infohash).strip().lower()
    data = _get_cached()
    if h in data:
        del data[h]
        _save(data)
        return True
    return False


def clear_bad_sources() -> None:
    _save({})

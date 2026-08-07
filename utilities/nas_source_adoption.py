"""NAS Source Adoption — replace collected debrid symlinks with verified NAS copies.

In Symlinked/Local mode, collected items are symlinked from a debrid mount into
a symlinked_files_path that Plex monitors. When the same media also exists as a
local transcode (e.g. Tdarr output) in a configured NAS / Network Drive path
that Plex monitors, the debrid symlink is redundant. This job deletes the
symlink and points the item at the NAS file — but ONLY after verifying Plex has
already scanned the NAS copy, so Plex never loses the media.

Runs from `ProgramRunner.task_nas_source_adoption` (nightly at a configurable
time) and from the Debrid Manager "Adopt NAS sources now" button (force=True).

Module-level imports are deliberately light (settings + media_category only);
the heavy modules are imported lazily so this file can be imported cheaply.
"""
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from utilities.settings import get_setting, get_nas_paths
from utilities.media_category import media_category

log = logging.getLogger('NAS_Source_Adoption')

# Category name -> settings key that toggles it.
_CATEGORY_SETTING_KEYS = {
    'movies':             'adopt_movies',
    'tv_shows':           'adopt_tv_shows',
    'anime_movies':       'adopt_anime_movies',
    'anime_tv':           'adopt_anime_tv',
    'documentary_movies': 'adopt_documentary_movies',
    'documentary_tv':     'adopt_documentary_tv',
}


# ── Daily latch ──────────────────────────────────────────────────────────────
def _latch_path() -> str:
    db_dir = os.environ.get('USER_DB_CONTENT', '/user/db_content')
    return os.path.join(db_dir, 'nas_source_adopt_latch.json')


def _load_last_run() -> str:
    try:
        with open(_latch_path(), 'r') as f:
            return (json.load(f) or {}).get('last_run', '')
    except Exception:
        return ''


def _mark_run_today() -> None:
    try:
        os.makedirs(os.path.dirname(_latch_path()), exist_ok=True)
        with open(_latch_path(), 'w') as f:
            json.dump({'last_run': datetime.now().strftime('%Y-%m-%d')}, f)
    except Exception as exc:
        logging.warning(f"[NAS_Adopt] Could not write latch file: {exc}")


def _should_run_today(run_time_str: str, last_run_date: str) -> bool:
    """Run once per day once the local clock passes run_time_str (HH:MM)."""
    now = datetime.now()
    if last_run_date == now.strftime('%Y-%m-%d'):
        return False
    try:
        run_time = datetime.strptime(run_time_str or '03:00', '%H:%M').time()
    except Exception:
        run_time = datetime.strptime('03:00', '%H:%M').time()
    return now.time() >= run_time


# ── Plex verification ────────────────────────────────────────────────────────
def _fetch_plex_basename_paths() -> Optional[Dict[str, set]]:
    """Fetch every file Plex has scanned as {basename_lower: set(full_path_lower)}.

    Returns None when Plex is unreachable or not configured — the caller must
    skip the run in that case (never adopt unverified). Uses the same raw-HTTP
    pattern as api_reconcile's _plex_section_filenames.
    """
    import requests
    import xml.etree.ElementTree as ET
    plex_url = get_setting('File Management', 'plex_url_for_symlink', '').rstrip('/')
    plex_token = get_setting('File Management', 'plex_token_for_symlink', '')
    if not plex_url or not plex_token:
        logging.warning("[NAS_Adopt] Plex URL/token not configured for symlink mode")
        return None
    headers = {'X-Plex-Token': plex_token, 'Accept': 'application/xml'}
    index: Dict[str, set] = {}
    try:
        sr = requests.get(f'{plex_url}/library/sections', headers=headers, timeout=10)
        sections: List[Tuple[str, str]] = []
        for d in ET.fromstring(sr.text).findall('Directory'):
            sid = d.get('key')
            if d.get('type') == 'movie':
                sections.append((sid, '1'))
            elif d.get('type') == 'show':
                sections.append((sid, '4'))
        for sid, mtype in sections:
            start = 0
            page_size = 5000
            while True:
                r2 = requests.get(
                    f'{plex_url}/library/sections/{sid}/all',
                    headers=headers,
                    params={'type': mtype, 'trash': '0',
                            'X-Plex-Container-Start': start,
                            'X-Plex-Container-Size': page_size},
                    timeout=30)
                root2 = ET.fromstring(r2.text)
                videos = root2.findall('Video')
                for video in videos:
                    for part in video.iter('Part'):
                        fp = part.get('file', '')
                        if fp:
                            fp = fp.rstrip('/').replace('\\', '/')
                            index.setdefault(os.path.basename(fp).lower(), set()).add(fp.lower())
                if len(videos) < page_size:
                    break
                start += page_size
    except Exception as exc:
        logging.warning(f"[NAS_Adopt] Plex fetch failed (skipping run): {exc}")
        return None
    return index


def _verify_nas_in_plex(nas_path: str,
                        plex_index: Optional[Dict[str, set]],
                        nas_prefixes_lower: Tuple[str, ...]) -> bool:
    """True when Plex has scanned this NAS file.

    Accepts an exact full-path match, or a basename match where at least one
    Plex path with that basename lives under a NAS prefix (tolerates Plex
    path-mapping differences and excludes the case where only the symlink entry
    carries the basename).
    """
    if not plex_index:
        return False
    norm = nas_path.rstrip('/').replace('\\', '/').lower()
    base = os.path.basename(norm)
    paths = plex_index.get(base)
    if not paths:
        return False
    if norm in paths:
        return True
    return any(p.startswith(prefix) for p in paths for prefix in nas_prefixes_lower)


# ── Adoption ─────────────────────────────────────────────────────────────────
def _adopt_item(item: Dict[str, Any], nas_path: str,
                delete_rd_torrent: bool) -> bool:
    """Delete the symlink and point the DB item at nas_path. Returns success."""
    old_loc = item.get('location_on_disk') or ''
    # 1. Delete the symlink (idempotent if already gone).
    try:
        if old_loc and os.path.islink(old_loc):
            os.unlink(old_loc)
            logging.info(f"[NAS_Adopt] Removed symlink: {old_loc}")
    except Exception as exc:
        logging.error(f"[NAS_Adopt] Failed to unlink {old_loc}: {exc}")

    # 2. Point the DB item at the NAS source.
    try:
        from database.database_writing import update_media_item
        update_media_item(item['id'],
                          location_on_disk=nas_path,
                          filled_by_file=os.path.basename(nas_path))
    except Exception as exc:
        logging.error(f"[NAS_Adopt] DB update failed for item {item.get('id')}: {exc}")
        return False  # never do Plex/torrent cleanup for an item that isn't pointing at NAS

    # 3. Drop the dead symlink entry from Plex (best-effort). Rescan only the
    #    old symlink's parent directory — the file itself is gone, but the dir
    #    still exists so Plex can locate the section. This avoids
    #    remove_file_from_plex, whose basename match could delete the NAS
    #    (das_pool) entry instead when Tdarr keeps the same filename.
    try:
        from utilities.plex_functions import scan_and_empty_plex_trash
        scan_and_empty_plex_trash(paths=[os.path.dirname(old_loc) if old_loc else old_loc])
    except Exception as exc:
        logging.warning(f"[NAS_Adopt] Plex rescan of {old_loc} failed: {exc}")

    # 4. Optionally remove the debrid torrent — only if no other item still uses it.
    if delete_rd_torrent:
        tid = item.get('filled_by_torrent_id')
        if tid:
            try:
                from database.core import get_db_connection
                conn = get_db_connection()
                try:
                    sibs = conn.execute(
                        "SELECT COUNT(*) FROM media_items WHERE filled_by_torrent_id = ?"
                        " AND state IN ('Collected','Upgrading','Checking') AND id != ?",
                        (tid, item.get('id'))
                    ).fetchone()[0]
                finally:
                    conn.close()
                if sibs:
                    logging.info(f"[NAS_Adopt] Skipping debrid removal of {tid} — {sibs} sibling item(s) still use it")
                else:
                    from debrid import get_debrid_provider
                    provider = get_debrid_provider()
                    if provider:
                        provider.remove_torrent(tid, removal_reason='Adopted NAS source')
                        logging.info(f"[NAS_Adopt] Removed debrid torrent {tid}")
                        from database.database_writing import update_media_item
                        update_media_item(item['id'], filled_by_torrent_id=None)
            except Exception as exc:
                logging.warning(f"[NAS_Adopt] Failed to remove debrid torrent {tid}: {exc}")

    return True


def run_nas_source_adoption(force: bool = False) -> Dict[str, Any]:
    """Run the NAS Source Adoption job. Returns a summary dict."""
    start_time = time.time()
    summary: Dict[str, Any] = {
        'enabled':               False,
        'adopted':               0,
        'skipped_no_nas':        0,
        'skipped_not_in_plex':   0,
        'skipped_category':      0,
        'errors':                0,
        'adopted_items':         [],
        'skipped_reason':        '',
        'elapsed_s':             0.0,
    }
    try:
        # ── Gates ──────────────────────────────────────────────────────
        if not get_setting('NAS Source Adoption', 'enabled', False):
            summary['skipped_reason'] = 'NAS Source Adoption is disabled in settings'
            logging.info(f"[NAS_Adopt] {summary['skipped_reason']}")
            return summary
        summary['enabled'] = True

        if get_setting('File Management', 'file_collection_management', 'Plex') != 'Symlinked/Local':
            summary['skipped_reason'] = 'Not in Symlinked/Local mode'
            logging.info(f"[NAS_Adopt] {summary['skipped_reason']}")
            return summary

        nas_prefixes = get_nas_paths()
        if not nas_prefixes:
            summary['skipped_reason'] = 'No NAS / Network Drive paths configured'
            logging.info(f"[NAS_Adopt] {summary['skipped_reason']}")
            return summary

        run_time_str = get_setting('NAS Source Adoption', 'run_time', '03:00')
        if not force:
            if not _should_run_today(run_time_str, _load_last_run()):
                summary['skipped_reason'] = 'Not the scheduled run time (or already run today)'
                logging.info(f"[NAS_Adopt] {summary['skipped_reason']}")
                return summary

        verify_in_plex = get_setting('NAS Source Adoption', 'verify_in_plex', True)
        delete_rd_torrent = get_setting('NAS Source Adoption', 'delete_rd_torrent', False)
        enabled_categories = {
            cat for cat, key in _CATEGORY_SETTING_KEYS.items()
            if get_setting('NAS Source Adoption', key, True)
        }
        nas_prefixes_lower = tuple(p.rstrip('/').lower() for p in nas_prefixes)

        # ── Load items ─────────────────────────────────────────────────
        from database.database_reading import get_all_media_items
        items = get_all_media_items(state='Collected') or []
        logging.info(f"[NAS_Adopt] Loaded {len(items)} collected items")

        # ── Build the NAS index once ───────────────────────────────────
        from utilities.local_library_scan import check_local_file_in_nas_paths, get_nas_file_index
        nas_index = get_nas_file_index(nas_prefixes)
        if not nas_index:
            summary['skipped_reason'] = 'No video files found under the NAS paths'
            logging.info(f"[NAS_Adopt] {summary['skipped_reason']}")
            return summary

        # ── Build the Plex index once (if verifying) ───────────────────
        plex_index = None
        if verify_in_plex:
            plex_index = _fetch_plex_basename_paths()
            if plex_index is None:
                summary['skipped_reason'] = 'Plex unreachable / not configured — skipping run (never adopt unverified)'
                logging.warning(f"[NAS_Adopt] {summary['skipped_reason']}")
                return summary

        for item in items:
            try:
                loc = (item.get('location_on_disk') or '').strip()
                # Skip items with no location, or already pointing at a NAS path
                # (those are already adopted).
                if not loc or loc.startswith(tuple(nas_prefixes)):
                    continue
                category = media_category(item)
                if category not in enabled_categories:
                    summary['skipped_category'] += 1
                    continue
                match = check_local_file_in_nas_paths(item, nas_index=nas_index)
                if not match:
                    summary['skipped_no_nas'] += 1
                    continue
                if verify_in_plex and not _verify_nas_in_plex(match['path'], plex_index, nas_prefixes_lower):
                    summary['skipped_not_in_plex'] += 1
                    continue
                if _adopt_item(item, match['path'], delete_rd_torrent):
                    summary['adopted'] += 1
                    summary['adopted_items'].append({
                        'id': item.get('id'),
                        'title': item.get('title') or '',
                        'from': loc,
                        'to': match['path'],
                    })
                else:
                    summary['errors'] += 1
            except Exception as exc:
                summary['errors'] += 1
                logging.error(f"[NAS_Adopt] Error processing item {item.get('id')}: {exc}")

        if not force:
            _mark_run_today()
        summary['elapsed_s'] = round(time.time() - start_time, 2)
        logging.info(
            f"[NAS_Adopt] Done: {summary['adopted']} adopted, "
            f"{summary['skipped_no_nas']} no NAS copy, "
            f"{summary['skipped_not_in_plex']} not yet in Plex, "
            f"{summary['skipped_category']} category-disabled, "
            f"{summary['errors']} errors ({summary['elapsed_s']}s)"
        )
        return summary
    except Exception as exc:
        summary['errors'] += 1
        summary['skipped_reason'] = str(exc)
        logging.error(f"[NAS_Adopt] Job failed: {exc}", exc_info=True)
        return summary

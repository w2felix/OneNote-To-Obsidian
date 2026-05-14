"""Content-hash-based analysis cache to avoid redundant API calls."""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_FILE = '.vision_ai_cache.json'
CACHE_VERSION = 1


def _cache_path(ai_notes_dir: Path) -> Path:
    return ai_notes_dir / CACHE_FILE


def load_cache(ai_notes_dir: Path) -> dict:
    path = _cache_path(ai_notes_dir)
    if not path.exists():
        return {'version': CACHE_VERSION, 'entries': {}}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('version') != CACHE_VERSION:
            return {'version': CACHE_VERSION, 'entries': {}}
        return data
    except (json.JSONDecodeError, OSError):
        return {'version': CACHE_VERSION, 'entries': {}}


def save_cache(ai_notes_dir: Path, cache: dict):
    path = _cache_path(ai_notes_dir)
    new_content = json.dumps(cache, indent=2, ensure_ascii=False)
    if path.exists() and path.read_text(encoding='utf-8') == new_content:
        return
    ai_notes_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(new_content, encoding='utf-8')


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def group_hash(filenames: list[str], images: dict[str, bytes]) -> str:
    """Hash for a group of files — sorted concatenation of individual hashes."""
    individual = sorted(content_hash(images[f]) for f in filenames if f in images)
    return hashlib.sha256(''.join(individual).encode()).hexdigest()


def is_cached(cache: dict, cache_key: str, current_hash: str,
              ai_notes_dir: Path) -> bool:
    """Check if an entry is cached AND its AI note file still exists."""
    entry = cache.get('entries', {}).get(cache_key)
    if not entry:
        return False
    if entry.get('content_hash') != current_hash:
        return False
    ai_note_file = entry.get('ai_note_file', '')
    if any(c in ai_note_file for c in '#[]'):
        return False
    if not (ai_notes_dir / ai_note_file).exists():
        return False
    return True


def update_cache(cache: dict, cache_key: str, current_hash: str,
                 ai_note_file: str, pipeline: str):
    cache.setdefault('entries', {})[cache_key] = {
        'content_hash': current_hash,
        'ai_note_file': ai_note_file,
        'analyzed': datetime.now(timezone.utc).isoformat(),
        'pipeline': pipeline,
    }

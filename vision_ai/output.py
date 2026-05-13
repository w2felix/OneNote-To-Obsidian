"""Write AI note files and inject callout links into parent markdown."""
from __future__ import annotations

import re
import logging
from datetime import datetime, timezone
from pathlib import Path

from vision_ai.client import VISION_MODEL

logger = logging.getLogger(__name__)

AI_NOTES_FOLDER = '_ai_notes'


def _sanitize_ai_content(content: str) -> str:
    """Sanitize AI-generated markdown to prevent injection of dangerous content."""
    # Remove javascript: protocol in links
    content = re.sub(r'\[([^\]]*)\]\(javascript:[^)]*\)', r'\1', content, flags=re.IGNORECASE)
    # Remove data: URIs in links (potential exfil vector)
    content = re.sub(r'\[([^\]]*)\]\(data:[^)]*\)', r'\1', content, flags=re.IGNORECASE)
    # Strip inline HTML script/iframe/object tags
    content = re.sub(r'<(script|iframe|object|embed|form)[^>]*>.*?</\1>', '', content,
                     flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r'<(script|iframe|object|embed|form)[^>]*/>', '', content,
                     flags=re.IGNORECASE)
    return content


def sanitize_filename(name: str, max_length: int = 120) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = name.strip('. ')
    return name[:max_length] if name else 'unnamed'


def generate_ai_note_filename(group) -> str:
    """Generate filename for an AI note based on the group."""
    if len(group.filenames) == 1:
        stem = Path(group.filenames[0]).stem
        return f"{sanitize_filename(stem)}_ai.md"

    # Grouped: worker_type + context/hash
    first_hash = Path(group.filenames[0]).stem[:8]
    if group.context:
        ctx_slug = sanitize_filename(group.context.split('\n')[0].strip()[:30])
        ctx_slug = re.sub(r'\s+', '_', ctx_slug).lower()
        return f"{group.worker_type}_{ctx_slug}_{first_hash}_ai.md"
    return f"{group.worker_type}_{first_hash}_ai.md"


def write_ai_note(ai_notes_dir: Path, filename: str, content: str,
                  group, model: str = None):
    """Write an AI note markdown file with frontmatter."""
    ai_notes_dir.mkdir(parents=True, exist_ok=True)
    filepath = ai_notes_dir / filename

    source_files_yaml = '\n'.join(f'  - "{f}"' for f in group.filenames)
    frontmatter_lines = [
        '---',
        f'source_type: {group.worker_type}',
        f'source_files:\n{source_files_yaml}',
        f'vision_model: {model or VISION_MODEL}',
        f'analyzed: {datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}',
    ]
    if group.context:
        ctx_escaped = group.context.split('\n')[0].strip().replace('"', '\\"')
        frontmatter_lines.append(f'context: "{ctx_escaped}"')
    frontmatter_lines.append('---')
    frontmatter_lines.append('')

    content = _sanitize_ai_content(content)
    full_content = '\n'.join(frontmatter_lines) + content
    filepath.write_text(full_content, encoding='utf-8')
    logger.info(f"  AI note written: {filename}")
    return filepath


def inject_callout_links(page_md_path: Path, groups_and_filenames: list[tuple]):
    """Inject Obsidian callout links for all groups in a single read/write pass.

    Args:
        page_md_path: Path to the parent markdown file
        groups_and_filenames: list of (group, ai_note_filename) tuples
    """
    md_text = page_md_path.read_text(encoding='utf-8')

    for group, ai_note_filename in groups_and_filenames:
        md_text = _inject_single_callout(md_text, group, ai_note_filename, page_md_path.name)

    page_md_path.write_text(md_text, encoding='utf-8')


def _find_embed_position(md_text: str, filename: str) -> int | None:
    """Find the end position of an embed reference for the given filename.

    Tries exact match first, then falls back to stem-based fuzzy matching.
    Returns the character position after the embed reference, or None.
    """
    # Exact match patterns
    exact_patterns = [
        f'![[_attachments/{filename}]]',
        f'[[_attachments/{filename}]]',
    ]
    for pattern in exact_patterns:
        pos = md_text.rfind(pattern)
        if pos >= 0:
            return pos + len(pattern)

    # Fuzzy match: search for the filename stem in any embed reference
    stem = Path(filename).stem
    # Match ![[_attachments/...stem...]] or [[_attachments/...stem...]]
    fuzzy_pattern = re.compile(
        r'(!?\[\[_attachments/[^\]]*' + re.escape(stem) + r'[^\]]*\]\])'
    )
    matches = list(fuzzy_pattern.finditer(md_text))
    if matches:
        last_match = matches[-1]
        return last_match.end()

    return None


def _inject_single_callout(md_text: str, group, ai_note_filename: str, page_name: str) -> str:
    """Inject a single callout link, returning the modified text."""
    last_filename = group.filenames[-1]
    insert_after = _find_embed_position(md_text, last_filename)

    # If last file not found, try earlier files in the group
    if insert_after is None and len(group.filenames) > 1:
        for fname in reversed(group.filenames[:-1]):
            insert_after = _find_embed_position(md_text, fname)
            if insert_after is not None:
                break

    if insert_after is None:
        logger.warning(f"  Could not find embed reference for {last_filename} in {page_name}")
        return md_text

    # Check if callout already exists for this AI note
    callout_ref = f'![[{AI_NOTES_FOLDER}/{ai_note_filename}]]'
    if callout_ref in md_text:
        return md_text

    # Build the callout
    n_files = len(group.filenames)
    label_parts = [group.worker_type.replace('_', ' ').title()]
    if group.context:
        ctx_short = group.context.split('\n')[0].strip()[:40]
        label_parts.append(ctx_short)
    if n_files > 1:
        label_parts.append(f"({n_files} files)")
    label = ' — '.join(label_parts)

    callout = f'\n\n> [!ai]- AI Analysis — {label}\n> ![[{AI_NOTES_FOLDER}/{ai_note_filename}]]'

    return md_text[:insert_after] + callout + md_text[insert_after:]

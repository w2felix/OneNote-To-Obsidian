"""Write AI note files and inject callout links into parent markdown."""
from __future__ import annotations

import re
import logging
from datetime import datetime, timezone
from pathlib import Path

from vision_ai.client import VISION_MODEL
from vision_ai.workers.base import AnalysisResult

logger = logging.getLogger(__name__)

AI_NOTES_FOLDER = '_ai_notes'


def _sanitize_ai_content(content: str) -> str:
    """Sanitize AI-generated markdown to prevent injection of dangerous content."""
    content = re.sub(r'\[([^\]]*)\]\(javascript:[^)]*\)', r'\1', content, flags=re.IGNORECASE)
    content = re.sub(r'\[([^\]]*)\]\(data:[^)]*\)', r'\1', content, flags=re.IGNORECASE)
    content = re.sub(r'<(script|iframe|object|embed|form)[^>]*>.*?</\1>', '', content,
                     flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r'<(script|iframe|object|embed|form)[^>]*/>', '', content,
                     flags=re.IGNORECASE)
    return content


def sanitize_filename(name: str, max_length: int = 120) -> str:
    name = re.sub(r'[<>:"/\\|?*#\[\]\x00-\x1f]', '_', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip('._ ')
    return name[:max_length] if name else 'unnamed'


def generate_ai_note_filename(group) -> str:
    """Generate filename for an AI note based on the group."""
    if len(group.filenames) == 1:
        stem = Path(group.filenames[0]).stem
        return f"{sanitize_filename(stem)}_ai.md"

    first_hash = Path(group.filenames[0]).stem[:8]
    if group.context:
        ctx_raw = group.context.split('\n')[0].strip().lstrip('#').strip()
        ctx_raw = re.sub(r'\[\[([^\]]+)\]\]', r'\1', ctx_raw)
        ctx_slug = sanitize_filename(ctx_raw[:30])
        ctx_slug = re.sub(r'\s+', '_', ctx_slug).strip('_').lower()
        if ctx_slug:
            return f"{group.worker_type}_{ctx_slug}_{first_hash}_ai.md"
    return f"{group.worker_type}_{first_hash}_ai.md"


def _yaml_escape(value: str) -> str:
    """Escape a string for YAML value (quote if special chars)."""
    if not value:
        return '""'
    if any(c in value for c in ':{}[]&*?|>!%@`#,\n"\''):
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _build_frontmatter(result: AnalysisResult, group, model: str = None) -> str:
    """Build YAML frontmatter from structured AnalysisResult."""
    lines = ['---']

    if result.title:
        lines.append(f'title: {_yaml_escape(result.title)}')
    if result.content_type:
        lines.append(f'content_type: {_yaml_escape(result.content_type)}')
    if result.authors:
        if len(result.authors) == 1:
            lines.append(f'authors: [{_yaml_escape(result.authors[0])}]')
        else:
            lines.append('authors:')
            for a in result.authors:
                lines.append(f'  - {_yaml_escape(a)}')
    if result.date:
        lines.append(f'date: {_yaml_escape(result.date)}')
    if result.key_points:
        lines.append('key_points:')
        for point in result.key_points:
            lines.append(f'  - {_yaml_escape(point)}')

    # Extra fields (worker-specific)
    for key, value in result.extra.items():
        if isinstance(value, list):
            lines.append(f'{key}:')
            for item in value:
                lines.append(f'  - {_yaml_escape(str(item))}')
        elif isinstance(value, int):
            lines.append(f'{key}: {value}')
        else:
            lines.append(f'{key}: {_yaml_escape(str(value))}')

    # Standard metadata
    lines.append(f'source_type: {group.worker_type}')
    lines.append('source_files:')
    for f in group.filenames:
        lines.append(f'  - "{f}"')
    lines.append(f'vision_model: {model or VISION_MODEL}')
    lines.append(f'analyzed: {datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}')
    if group.context:
        ctx_escaped = group.context.split('\n')[0].strip()
        lines.append(f'context: {_yaml_escape(ctx_escaped)}')
    lines.append('cssclasses:\n  - ai-generated')
    lines.append('graph_exclude: true')

    lines.append('---')
    return '\n'.join(lines)


def write_ai_note(ai_notes_dir: Path, filename: str, result: AnalysisResult,
                  group, model: str = None):
    """Write an AI note markdown file with enriched YAML frontmatter."""
    ai_notes_dir.mkdir(parents=True, exist_ok=True)
    filepath = ai_notes_dir / filename

    frontmatter = _build_frontmatter(result, group, model)

    body = ""
    if result.body:
        body = _sanitize_ai_content(result.body)

    full_content = frontmatter + '\n\n' + body if body else frontmatter + '\n'
    filepath.write_text(full_content, encoding='utf-8')
    logger.info(f"  AI note written: {filename}")
    return filepath


def inject_callout_links(page_md_path: Path, groups_and_filenames: list[tuple]):
    """Inject Obsidian callout links for all groups in a single read/write pass."""
    md_text = page_md_path.read_text(encoding='utf-8')

    md_text = _remove_broken_callouts(md_text)

    for group, ai_note_filename in groups_and_filenames:
        md_text = _inject_single_callout(md_text, group, ai_note_filename, page_md_path.name)

    page_md_path.write_text(md_text, encoding='utf-8')


_BROKEN_CALLOUT_RE = re.compile(
    r'\n\n> \[!ai\]- AI Analysis[^\n]*\n> !\[\[_ai_notes/[^\]]*[#\[\]][^\]]*\]\](?:\n|$)',
)


def _remove_broken_callouts(md_text: str) -> str:
    """Remove callout blocks whose AI note filenames contain #, [, or ]."""
    return _BROKEN_CALLOUT_RE.sub('\n', md_text)


def _find_embed_position(md_text: str, filename: str) -> int | None:
    """Find the end position of an embed reference for the given filename."""
    exact_patterns = [
        f'![[_attachments/{filename}]]',
        f'[[_attachments/{filename}]]',
    ]
    for pattern in exact_patterns:
        pos = md_text.rfind(pattern)
        if pos >= 0:
            return pos + len(pattern)

    stem = Path(filename).stem
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

    if insert_after is None and len(group.filenames) > 1:
        for fname in reversed(group.filenames[:-1]):
            insert_after = _find_embed_position(md_text, fname)
            if insert_after is not None:
                break

    if insert_after is None:
        logger.warning(f"  Could not find embed reference for {last_filename} in {page_name}")
        return md_text

    callout_ref = f'![[{AI_NOTES_FOLDER}/{ai_note_filename}]]'
    if callout_ref in md_text:
        return md_text

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

"""Post-processing script to fix known issues in the obsidian_export.

Applies fixes to existing exported files without re-running the full pipeline:
1. Decode HTML entities in YAML and content (&amp; &lt; &gt; &quot;)
2. Remove false disease wiki-links (common words linked to rare diseases)
3. Convert OneNote citation format (From <[URL]>) to standard markdown
4. Detect and fence unescaped code blocks
5. Remove duplicate entity index files (keep canonical only)
6. Strip unnecessary markdown escape characters outside code fences
7. Add language specifiers to bare code fences

Usage:
    python tools/fix_export.py [--dry-run] [export_dir]
"""

import argparse
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from onenote_to_obsidian import _unescape_code_line, _looks_like_continuation


# Disease names that were incorrectly used as aliases for common words
_FALSE_DISEASE_LINKS = re.compile(
    r'\[\['
    r'(?:PLA2G6-associated neurodegeneration'
    r'|disorder of sexual differentiation'
    r'|keratosis linearis-ichthyosis congenita-sclerosing keratoderma syndrome'
    r'|gastric antral vascular ectasia'
    r'|peritoneal multicystic mesothelioma'
    r'|[^|\]]{20,})'  # Any long disease name (>20 chars) used as alias for short word
    r'\|'
    r'([a-zA-Z]{3,8})'  # The actual common word (3-8 chars)
    r'\]\]'
)

# OneNote citation format (URL may contain parentheses)
_ONENOTE_CITATION = re.compile(r'From <\[([^\]]+)\]\((.+?)\)\\?>')

# HTML entities in YAML and content
_HTML_ENTITIES = {
    '&amp;': '&',
    '&lt;': '<',
    '&gt;': '>',
    '&quot;': '"',
}

# Code detection patterns (for rescue pass)
_CODE_RESCUE_RE = re.compile(
    r'\w+\s*<-\s'
    r'|%>%'
    r'|\bc\('
    r'|\blibrary\('
    r'|\bimport\s'
    r'|\bdef\s'
    r'|\bfunction\s*\('
    r'|\\#.*\\#'
)

# Common words that should NOT be disease-linked
_COMMON_WORDS = {
    'plan', 'card', 'gave', 'klick', 'complete', 'gain', 'loss', 'dose',
    'test', 'note', 'link', 'type', 'goal', 'edge', 'diet', 'page',
    'frame', 'plot', 'send', 'call', 'lead', 'core', 'role', 'date',
    'not rare',
}

# --- Escaped character cleanup ---
# Unnecessary escapes: \_ \~ \> \[ \] outside code fences
# Context-aware: only remove when not serving a formatting purpose
_ESCAPED_UNDERSCORE = re.compile(r'\\_')
_ESCAPED_TILDE_APPROX = re.compile(r'\\~(\d)')  # \~24% → ~24%
_ESCAPED_TILDE_STANDALONE = re.compile(r'\\~')
_ESCAPED_GT = re.compile(r'\\>')
_ESCAPED_BRACKET_OPEN = re.compile(r'\\\[')
_ESCAPED_BRACKET_CLOSE = re.compile(r'\\\]')

# Language detection for bare code fences
_LANG_PATTERNS = {
    'r': re.compile(
        r'library\(|<-\s|%>%|ggplot\(|geom_|aes\(|dplyr::|tidyverse'
        r'|mutate\(|filter\(|select\(|summarise\(|tibble\(|readr::'
    ),
    'python': re.compile(
        r'^import\s|^from\s\w+\simport|def\s\w+\(|class\s\w+[:(]'
        r'|print\(|pandas|numpy|plt\.|\.fit\(|\.predict\(',
        re.MULTILINE,
    ),
    'bash': re.compile(
        r'^(sudo\s|apt\s|yum\s|conda\s|mamba\s|pip\s|git\s|ssh\s|scp\s'
        r'|cd\s|ls\s|mkdir\s|rm\s|cp\s|mv\s|chmod\s|export\s|source\s|eval\s)',
        re.MULTILINE,
    ),
    'powershell': re.compile(
        r'\$env:|Set-Item|Get-Item|\[Environment\]|Install-Module'
        r'|Write-Host|New-Object|Get-Content',
    ),
    'sql': re.compile(
        r'^SELECT\s|^INSERT\s|^UPDATE\s|^CREATE\s|^ALTER\s|^DROP\s|^FROM\s',
        re.MULTILINE | re.IGNORECASE,
    ),
}


def fix_html_entities(content: str) -> str:
    """Decode HTML entities in both YAML frontmatter and body content."""
    for entity, char in _HTML_ENTITIES.items():
        content = content.replace(entity, char)
    return content


def fix_false_disease_links(content: str) -> str:
    """Remove wiki-links where a disease name is used as alias for a common word."""
    def _replace(m):
        word = m.group(1)
        if word.lower() in _COMMON_WORDS or len(word) <= 6:
            return word
        return m.group(0)

    return _FALSE_DISEASE_LINKS.sub(_replace, content)


def fix_onenote_citations(content: str) -> str:
    """Convert OneNote citation format to standard markdown links."""
    return _ONENOTE_CITATION.sub(r'Source: [\1](\2)', content)


def fix_code_blocks(content: str) -> str:
    """Detect consecutive escaped code lines and wrap in fenced code blocks."""
    if not _CODE_RESCUE_RE.search(content):
        return content
    lines = content.split('\n')
    result = []
    in_fence = False
    code_buf = []

    def _flush_code(buf):
        if len(buf) >= 2:
            result.append('```')
            for ln in buf:
                result.append(_unescape_code_line(ln))
            result.append('```')
        else:
            result.extend(buf)

    for line in lines:
        if line.startswith('```'):
            if code_buf:
                _flush_code(code_buf)
                code_buf = []
            in_fence = not in_fence
            result.append(line)
            continue

        if in_fence:
            result.append(line)
            continue

        if _CODE_RESCUE_RE.search(line) or (code_buf and _looks_like_continuation(line)):
            code_buf.append(line)
        else:
            if code_buf:
                _flush_code(code_buf)
                code_buf = []
            result.append(line)

    if code_buf:
        _flush_code(code_buf)

    return '\n'.join(result)


def fix_gene_alias_entities(content: str) -> str:
    """Fix 'ALIAS (CANONICAL)' pattern in entities frontmatter to just CANONICAL."""
    # Match pattern in YAML entities section: "ALIAS (CANONICAL)" -> "CANONICAL"
    content = re.sub(
        r'^(\s*-\s*")[A-Z0-9-]+ \(([A-Z0-9-]+)\)"',
        r'\1\2"',
        content,
        flags=re.MULTILINE,
    )
    # Also fix in wikilinks: [[ALIAS (CANONICAL)|display]] -> [[CANONICAL|display]]
    content = re.sub(
        r'\[\[([A-Z0-9-]+) \(([A-Z0-9-]+)\)\|([^\]]+)\]\]',
        r'[[\2|\3]]',
        content,
    )
    content = re.sub(
        r'\[\[([A-Z0-9-]+) \(([A-Z0-9-]+)\)\]\]',
        r'[[\2|\1]]',
        content,
    )
    # Deduplicate consecutive identical YAML list entries
    content = re.sub(
        r'^(\s*-\s*"[^"]+")$\n\1$',
        r'\1',
        content,
        flags=re.MULTILINE,
    )
    return content


def fix_disease_frontmatter(content: str) -> str:
    """Remove false positive diseases from YAML frontmatter entities section."""
    if '  diseases:' not in content:
        return content
    lines = content.split('\n')
    in_frontmatter = False
    in_diseases = False
    result = []

    for line in lines:
        if line.strip() == '---':
            if not in_frontmatter:
                in_frontmatter = True
            else:
                in_frontmatter = False
                in_diseases = False
            result.append(line)
            continue

        if in_frontmatter:
            if line.strip() == 'diseases:':
                in_diseases = True
                result.append(line)
                continue
            elif in_diseases and line.startswith('    - '):
                disease = line.strip().lstrip('- ').strip('"\'')
                if disease.lower() in _COMMON_WORDS:
                    continue
                # Skip known false positive diseases (rare diseases as word aliases)
                if disease in (
                    'PLA2G6-associated neurodegeneration',
                    'disorder of sexual differentiation',
                    'keratosis linearis-ichthyosis congenita-sclerosing keratoderma syndrome',
                    'gastric antral vascular ectasia',
                    'peritoneal multicystic mesothelioma',
                ):
                    continue
            elif in_diseases and not line.startswith('    '):
                in_diseases = False

        result.append(line)

    return '\n'.join(result)


def fix_escaped_characters(content: str) -> str:
    """Strip unnecessary markdown escape characters outside code fences.

    The pipeline's _escape_md_text escapes all markdown-special chars, but many
    are unnecessary in the output and create visible backslashes in Obsidian.
    This removes escapes outside code fences (both fenced and inline).
    """
    lines = content.split('\n')
    result = []
    in_fence = False
    in_frontmatter = False
    frontmatter_count = 0

    for line in lines:
        stripped = line.strip()

        # Track frontmatter boundaries
        if stripped == '---':
            frontmatter_count += 1
            in_frontmatter = frontmatter_count == 1
            result.append(line)
            continue
        if frontmatter_count == 1:
            # Inside frontmatter — don't touch
            result.append(line)
            continue

        # Track code fences
        if stripped.startswith('```'):
            in_fence = not in_fence
            result.append(line)
            continue

        if in_fence:
            result.append(line)
            continue

        # Outside code fences: strip unnecessary escapes
        # Protect inline code spans and wikilinks from modification
        parts = re.split(r'(`[^`\n]+`|\[\[[^\]]+\]\])', line)
        cleaned = []
        for i, part in enumerate(parts):
            if i % 2 == 1:
                # Protected segment (inline code or wikilink)
                cleaned.append(part)
            else:
                part = _ESCAPED_UNDERSCORE.sub('_', part)
                part = _ESCAPED_TILDE_APPROX.sub(r'~\1', part)
                part = _ESCAPED_GT.sub('>', part)
                part = _ESCAPED_BRACKET_OPEN.sub('[', part)
                part = _ESCAPED_BRACKET_CLOSE.sub(']', part)
                # Standalone \~ (not before digit) — only remove if not in R formula context
                if '~' in part and '<-' not in line and '%>%' not in line:
                    part = _ESCAPED_TILDE_STANDALONE.sub('~', part)
                cleaned.append(part)
        result.append(''.join(cleaned))

    return '\n'.join(result)


def fix_code_fence_languages(content: str) -> str:
    """Add language specifiers to bare code fences based on content analysis."""
    lines = content.split('\n')
    result = []
    i = 0

    while i < len(lines):
        line = lines[i]
        # Found a bare opening fence
        if line.strip() == '```':
            # Collect the code block content
            block_lines = [line]
            code_content = []
            j = i + 1
            while j < len(lines):
                block_lines.append(lines[j])
                if lines[j].strip() == '```':
                    break
                code_content.append(lines[j])
                j += 1

            if code_content:
                code_text = '\n'.join(code_content)
                detected_lang = _detect_code_language(code_text)
                if detected_lang:
                    block_lines[0] = f'```{detected_lang}'

            result.extend(block_lines)
            i = j + 1
        else:
            result.append(line)
            i += 1

    return '\n'.join(result)


def _detect_code_language(code: str) -> str:
    """Detect programming language from code content."""
    for lang, pattern in _LANG_PATTERNS.items():
        if pattern.search(code):
            return lang
    return ''


def process_file(filepath: Path, dry_run: bool = False) -> bool:
    """Apply all fixes to a single markdown file. Returns True if modified."""
    try:
        content = filepath.read_text(encoding='utf-8')
    except (UnicodeDecodeError, PermissionError):
        return False

    original = content

    content = fix_html_entities(content)
    content = fix_false_disease_links(content)
    content = fix_disease_frontmatter(content)
    content = fix_onenote_citations(content)
    content = fix_gene_alias_entities(content)
    content = fix_code_blocks(content)
    content = fix_escaped_characters(content)
    content = fix_code_fence_languages(content)

    if content != original:
        if not dry_run:
            filepath.write_text(content, encoding='utf-8')
        return True
    return False


def add_ai_note_backlinks(export_dir: Path, dry_run: bool = False) -> int:
    """Add backlinks from parent pages to their AI analysis notes."""
    added = 0
    ai_notes_marker = '> [!ai-analysis]'

    for ai_dir in export_dir.rglob('_ai_notes'):
        if not ai_dir.is_dir():
            continue
        parent_dir = ai_dir.parent
        ai_files = sorted(ai_dir.glob('*_ai.md'))
        if not ai_files:
            continue

        # Find parent markdown files in the same directory
        for parent_md in parent_dir.glob('*.md'):
            if parent_md.parent.name == '_ai_notes':
                continue
            try:
                content = parent_md.read_text(encoding='utf-8')
            except (UnicodeDecodeError, PermissionError):
                continue

            # Skip if already has AI note backlinks
            if ai_notes_marker in content:
                continue

            # Find AI notes that reference images embedded in this page
            relevant_ai = []
            for ai_file in ai_files:
                try:
                    ai_content = ai_file.read_text(encoding='utf-8')
                except (UnicodeDecodeError, PermissionError):
                    continue
                # Check if any source_files match images in this page
                if 'source_files:' in ai_content:
                    for line in ai_content.split('\n'):
                        if line.strip().startswith('- "') and line.strip().endswith('"'):
                            img_name = line.strip().removeprefix('- "').removesuffix('"')
                            if f'![[_attachments/{img_name}]]' in content:
                                relevant_ai.append(ai_file.stem)
                                break

            if not relevant_ai:
                continue

            # Add callout at end of file
            links = ', '.join(f'[[_ai_notes/{name}]]' for name in relevant_ai)
            callout = f'\n\n{ai_notes_marker} AI Analysis\n> {links}\n'
            new_content = content.rstrip() + callout

            if not dry_run:
                parent_md.write_text(new_content, encoding='utf-8')
            added += 1

    return added


def remove_empty_untitled_pages(export_dir: Path, dry_run: bool = False) -> int:
    """Remove 'Untitled page.md' files that have no meaningful body content."""
    removed = 0
    skip_names = {'Untitled page.md', 'Untitled page (2).md', 'Untitled page (3).md',
                  'Untitled.md', 'Neuer Abschnitt.md'}

    for md_file in export_dir.rglob('*.md'):
        if md_file.name not in skip_names:
            continue
        # Check if it has meaningful content beyond frontmatter
        try:
            content = md_file.read_text(encoding='utf-8')
        except (UnicodeDecodeError, PermissionError):
            continue

        # Split off frontmatter
        body = content
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                body = parts[2]

        # Strip image embeds and whitespace to check for real content
        body_clean = re.sub(r'!\[\[_attachments/[^\]]+\]\]', '', body).strip()
        if len(body_clean) < 20:
            if not dry_run:
                md_file.unlink()
            removed += 1
            print(f"  {'Would remove' if dry_run else 'Removed'}: {md_file.relative_to(export_dir)}")

    return removed


def remove_duplicate_entity_files(entity_dir: Path, dry_run: bool = False) -> int:
    """Remove 'ALIAS (CANONICAL).md' files that duplicate the canonical file."""
    removed = 0
    if not entity_dir.exists():
        return 0

    for subdir in entity_dir.iterdir():
        if not subdir.is_dir():
            continue
        for f in subdir.glob('* (*).md'):
            # Extract the canonical name from parentheses
            match = re.match(r'.+ \((.+)\)\.md$', f.name)
            if match:
                canonical_name = match.group(1)
                canonical_file = subdir / f'{canonical_name}.md'
                if canonical_file.exists():
                    if not dry_run:
                        f.unlink()
                    removed += 1
                    print(f"  {'Would remove' if dry_run else 'Removed'}: {f.relative_to(entity_dir)}")

    return removed


def main():
    parser = argparse.ArgumentParser(description='Fix known issues in obsidian_export')
    parser.add_argument('export_dir', nargs='?',
                        default='obsidian_export',
                        help='Path to the obsidian_export directory')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be changed without modifying files')
    args = parser.parse_args()

    export_dir = Path(args.export_dir)
    if not export_dir.exists():
        print(f"Error: {export_dir} does not exist")
        sys.exit(1)

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Processing {export_dir}...")

    # Fix all markdown files
    md_files = list(export_dir.rglob('*.md'))
    modified = 0
    for i, filepath in enumerate(md_files):
        if process_file(filepath, dry_run=args.dry_run):
            modified += 1
        if (i + 1) % 500 == 0:
            print(f"  Processed {i + 1}/{len(md_files)} files...")

    print(f"\n{'Would modify' if args.dry_run else 'Modified'} {modified}/{len(md_files)} files")

    # Add AI note backlinks
    print(f"\nAdding AI note backlinks...")
    ai_links_added = add_ai_note_backlinks(export_dir, dry_run=args.dry_run)
    print(f"{'Would add' if args.dry_run else 'Added'} backlinks to {ai_links_added} pages")

    # Remove empty untitled pages
    print(f"\nCleaning empty untitled pages...")
    removed_untitled = remove_empty_untitled_pages(export_dir, dry_run=args.dry_run)
    print(f"{'Would remove' if args.dry_run else 'Removed'} {removed_untitled} empty untitled pages")

    # Remove duplicate entity index files
    entity_dir = export_dir / '_entity_index'
    if entity_dir.exists():
        print(f"\nCleaning duplicate entity index files...")
        removed = remove_duplicate_entity_files(entity_dir, dry_run=args.dry_run)
        print(f"{'Would remove' if args.dry_run else 'Removed'} {removed} duplicate entity files")

    print("\nDone!")


if __name__ == '__main__':
    main()

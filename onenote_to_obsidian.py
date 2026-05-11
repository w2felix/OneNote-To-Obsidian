"""
OneNote to Obsidian Sync Tool

Exports OneNote notebooks to Obsidian-compatible Markdown files via PowerShell COM automation.
Supports incremental sync with conflict resolution.

Usage:
    python onenote_to_obsidian.py                    # Full sync (all notebooks)
    python onenote_to_obsidian.py --dry-run          # Preview changes
    python onenote_to_obsidian.py --notebooks "X"    # Specific notebooks
    python onenote_to_obsidian.py --vault-mode multi # Separate vaults per notebook
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import html as html_mod

from bs4 import BeautifulSoup

# ============================================================================
# Constants
# ============================================================================

NS = {'one': 'http://schemas.microsoft.com/office/onenote/2013/onenote'}
SYNC_STATE_FILE = '.sync_state.json'
SYNC_STATE_VERSION = 1
ATTACHMENTS_FOLDER = '_attachments'


# ============================================================================
# CLI
# ============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description='Sync OneNote notebooks to Obsidian Markdown files.'
    )
    parser.add_argument(
        '--output-dir', type=Path, default=Path('./obsidian_export'),
        help='Output directory for the Obsidian vault (default: ./obsidian_export)'
    )
    parser.add_argument(
        '--notebooks', nargs='+', default=None,
        help='Specific notebook names to sync (default: all)'
    )
    parser.add_argument(
        '--vault-mode', choices=['single', 'multi'], default='single',
        help='single = one vault with all notebooks; multi = separate vault per notebook'
    )
    parser.add_argument(
        '--skip-images', action='store_true',
        help='Skip image extraction (text only, faster)'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Show what would change without writing anything'
    )
    parser.add_argument(
        '--force-reexport', action='store_true',
        help='Re-export pages even if previously deleted from Obsidian'
    )
    return parser.parse_args()


# ============================================================================
# Sync State
# ============================================================================

def load_sync_state(output_dir: Path) -> dict:
    path = output_dir / SYNC_STATE_FILE
    if not path.exists():
        return {'version': SYNC_STATE_VERSION, 'last_sync': None, 'pages': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    return data


def save_sync_state(state: dict, output_dir: Path):
    state['last_sync'] = datetime.now(timezone.utc).isoformat()
    path = output_dir / SYNC_STATE_FILE
    tmp_path = path.with_suffix('.json.tmp')
    tmp_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding='utf-8')
    tmp_path.replace(path)


def page_key(page_id: str) -> str:
    return hashlib.sha256(page_id.encode()).hexdigest()[:16]


def file_hash(filepath: Path) -> str:
    if not filepath.exists():
        return ''
    return hashlib.sha256(filepath.read_bytes()).hexdigest()[:16]


# ============================================================================
# PowerShell COM Export
# ============================================================================

def generate_export_script(temp_dir: Path, page_ids: list[str] | None = None) -> str:
    """Generate a PowerShell script that exports hierarchy and page content."""
    temp_dir_escaped = str(temp_dir).replace('\\', '\\\\')

    if page_ids:
        page_id_list = '\n'.join(f'    "{pid}"' for pid in page_ids)
        page_export_block = f'''
$pageIds = @(
{page_id_list}
)
$count = 0
foreach ($pid in $pageIds) {{
    $count++
    try {{
        [string]$pageXml = ""
        $onenote.GetPageContent($pid, [ref]$pageXml, 1)
        $hash = [System.BitConverter]::ToString(
            [System.Security.Cryptography.SHA256]::Create().ComputeHash(
                [System.Text.Encoding]::UTF8.GetBytes($pid)
            )
        ).Replace("-","").Substring(0,16).ToLower()
        $filename = "{temp_dir_escaped}\\\\$hash.xml"
        [System.IO.File]::WriteAllText($filename, $pageXml, [System.Text.Encoding]::UTF8)
        Write-Output "PROGRESS:$count/$($pageIds.Count)"
    }} catch {{
        Write-Output "ERROR:$pid`:$($_.Exception.Message)"
    }}
}}
'''
    else:
        page_export_block = '''
[xml]$doc = $hierarchy
$nsm = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
$nsm.AddNamespace("one", "http://schemas.microsoft.com/office/onenote/2013/onenote")
$pages = $doc.SelectNodes("//one:Page", $nsm)
$count = 0
foreach ($page in $pages) {
    $count++
    try {
        [string]$pageXml = ""
        $onenote.GetPageContent($page.ID, [ref]$pageXml, 1)
        $hash = [System.BitConverter]::ToString(
            [System.Security.Cryptography.SHA256]::Create().ComputeHash(
                [System.Text.Encoding]::UTF8.GetBytes($page.ID)
            )
        ).Replace("-","").Substring(0,16).ToLower()
        $filename = "''' + temp_dir_escaped + '''\\$hash.xml"
        [System.IO.File]::WriteAllText($filename, $pageXml, [System.Text.Encoding]::UTF8)
        Write-Output "PROGRESS:$count/$($pages.Count):$($page.name)"
    } catch {
        Write-Output "ERROR:$($page.ID):$($_.Exception.Message)"
    }
}
'''

    return f'''[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$onenote = New-Object -ComObject OneNote.Application

# Export hierarchy
[string]$hierarchy = ""
$onenote.GetHierarchy("", 4, [ref]$hierarchy)
[System.IO.File]::WriteAllText("{temp_dir_escaped}\\\\hierarchy.xml", $hierarchy, [System.Text.Encoding]::UTF8)
Write-Output "HIERARCHY_DONE"

# Export pages
{page_export_block}
Write-Output "DONE"
'''


def safe_print(text, **kwargs):
    """Print with fallback for encoding errors on Windows console."""
    try:
        print(text, **kwargs)
    except UnicodeEncodeError:
        print(text.encode('ascii', errors='replace').decode('ascii'), **kwargs)


def run_powershell_export(temp_dir: Path, page_ids: list[str] | None = None) -> list[str]:
    """Run PowerShell export script, return list of errors."""
    script = generate_export_script(temp_dir, page_ids)
    script_path = temp_dir / 'export.ps1'
    script_path.write_text(script, encoding='utf-8')

    process = subprocess.Popen(
        ['powershell', '-ExecutionPolicy', 'Bypass', '-File', str(script_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace'
    )

    errors = []
    for line in process.stdout:
        line = line.strip()
        if line.startswith('PROGRESS:'):
            parts = line[9:].split(':', 1)
            name = parts[1] if len(parts) > 1 else ''
            safe_print(f'\r  Exporting pages {parts[0]} {name[:50]:<50}', end='', flush=True)
        elif line.startswith('ERROR:'):
            errors.append(line[6:])
        elif line == 'HIERARCHY_DONE':
            safe_print('  Hierarchy exported.')
        elif line == 'DONE':
            safe_print('\n  Export complete.')

    process.wait()
    if process.returncode != 0:
        stderr = process.stderr.read()
        safe_print(f'  PowerShell warnings: {stderr[:200]}', file=sys.stderr)

    return errors


# ============================================================================
# Hierarchy Parsing
# ============================================================================

def parse_hierarchy(hierarchy_path: Path) -> list[dict]:
    """Parse hierarchy XML into structured notebook list."""
    tree = ET.parse(hierarchy_path)
    root = tree.getroot()

    notebooks = []
    for nb_elem in root.findall('one:Notebook', NS):
        notebook = {
            'name': nb_elem.get('name'),
            'sections': [],
            'section_groups': []
        }
        _parse_container(nb_elem, notebook)
        notebooks.append(notebook)

    return notebooks


def _parse_container(elem, container):
    """Recursively parse section groups and sections."""
    for sg in elem.findall('one:SectionGroup', NS):
        if sg.get('isRecycleBin') == 'true' or sg.get('name') == 'OneNote_RecycleBin':
            continue
        group = {'name': sg.get('name'), 'sections': [], 'section_groups': []}
        _parse_container(sg, group)
        container['section_groups'].append(group)

    for sec in elem.findall('one:Section', NS):
        section = {'name': sec.get('name'), 'pages': []}
        for page in sec.findall('one:Page', NS):
            section['pages'].append({
                'id': page.get('ID'),
                'name': page.get('name') or 'Untitled',
                'level': int(page.get('pageLevel', '1')),
                'created': page.get('dateTime', ''),
                'modified': page.get('lastModifiedTime', ''),
            })
        container['sections'].append(section)


def collect_all_pages(notebooks: list[dict], notebook_filter: list[str] | None = None) -> list[dict]:
    """Flatten hierarchy into list of pages with path info."""
    pages = []

    for nb in notebooks:
        if notebook_filter and nb['name'] not in notebook_filter:
            continue
        _collect_from_container(nb, nb['name'], pages)

    return pages


def _collect_from_container(container, path_prefix: str, pages: list):
    for sg in container.get('section_groups', []):
        sg_path = f"{path_prefix}/{sg['name']}"
        _collect_from_container(sg, sg_path, pages)

    for sec in container.get('sections', []):
        sec_path = f"{path_prefix}/{sec['name']}"
        parent_stack = []
        for page in sec['pages']:
            level = page['level']
            while len(parent_stack) >= level:
                parent_stack.pop()
            parent_name = parent_stack[-1] if parent_stack else None
            parent_stack.append(page['name'])

            pages.append({
                **page,
                'section_path': sec_path,
                'parent_page': parent_name,
            })


# ============================================================================
# Markdown Conversion
# ============================================================================

def convert_page_xml(xml_path: Path, page_info: dict, skip_images: bool = False) -> tuple[str, dict]:
    """Convert OneNote page XML to Markdown string + dict of images."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    ns_uri = 'http://schemas.microsoft.com/office/onenote/2013/onenote'
    ns = {'one': ns_uri}

    # Build style map from QuickStyleDefs
    style_map = {}
    for style_def in root.findall('one:QuickStyleDef', ns):
        idx = style_def.get('index')
        name = style_def.get('name', 'p')
        style_map[idx] = name

    # Get title
    title = page_info['name']
    title_elem = root.find('.//one:Title//one:T', ns)
    if title_elem is not None and title_elem.text:
        title = _extract_cdata_text(title_elem.text)

    # Build frontmatter
    lines = ['---']
    lines.append(f'title: "{title.replace(chr(34), chr(39))}"')
    if page_info.get('created'):
        lines.append(f'created: {page_info["created"]}')
    if page_info.get('modified'):
        lines.append(f'modified: {page_info["modified"]}')
    lines.append('source: OneNote')
    if page_info.get('parent_page'):
        safe_parent = page_info['parent_page'].replace('"', "'")
        lines.append(f'parent: "[[{safe_parent}]]"')
    lines.append('---')
    lines.append('')
    lines.append(f'# {title}')
    lines.append('')

    # Process all outlines
    images = {}
    for outline in root.findall('one:Outline', ns):
        oe_children = outline.find('one:OEChildren', ns)
        if oe_children is not None:
            content = _convert_oe_children(oe_children, ns, style_map, images, skip_images, 0)
            lines.append(content)
            lines.append('')

    # Also check for outlines that have direct OE children (no OEChildren wrapper)
    for outline in root.findall('one:Outline', ns):
        if outline.find('one:OEChildren', ns) is None:
            for oe in outline.findall('one:OE', ns):
                line = _convert_oe(oe, ns, style_map, images, skip_images, 0)
                if line:
                    lines.append(line)
            lines.append('')

    markdown = '\n'.join(lines)
    markdown = re.sub(r'\n{3,}', '\n\n', markdown)
    return markdown, images


def _convert_oe_children(elem, ns, style_map, images, skip_images, indent) -> str:
    """Convert an OEChildren element to markdown."""
    lines = []
    for oe in elem.findall('one:OE', ns):
        line = _convert_oe(oe, ns, style_map, images, skip_images, indent)
        if line is not None:
            lines.append(line)

        # Nested OEChildren within this OE
        nested = oe.find('one:OEChildren', ns)
        if nested is not None:
            nested_content = _convert_oe_children(nested, ns, style_map, images, skip_images, indent + 1)
            if nested_content:
                lines.append(nested_content)

    return '\n'.join(lines)


def _convert_oe(oe_elem, ns, style_map, images, skip_images, indent) -> str | None:
    """Convert a single OE element to a markdown line."""
    # Check for table
    table = oe_elem.find('one:Table', ns)
    if table is not None:
        return _convert_table(table, ns)

    # Check for image
    image = oe_elem.find('one:Image', ns)

    # Get all text elements (there can be multiple T elements in one OE)
    text_parts = []
    for t_elem in oe_elem.findall('one:T', ns):
        if t_elem.text:
            text_parts.append(_convert_cdata_html(t_elem.text))

    text = ''.join(text_parts)

    # Check for list
    list_elem = oe_elem.find('one:List', ns)
    bullet = list_elem.find('one:Bullet', ns) if list_elem is not None else None
    number = list_elem.find('one:Number', ns) if list_elem is not None else None

    # Check for tag (checkbox)
    tag = oe_elem.find('one:Tag', ns)

    # Determine style/heading
    style_idx = oe_elem.get('quickStyleIndex', '')
    style_name = style_map.get(style_idx, 'p')

    # Build the line
    prefix = ''
    indent_str = '  ' * indent

    if tag is not None:
        completed = tag.get('completed', 'false') == 'true'
        checkbox = '[x] ' if completed else '[ ] '
        prefix = f'{indent_str}- {checkbox}'
    elif bullet is not None:
        prefix = f'{indent_str}- '
    elif number is not None:
        prefix = f'{indent_str}1. '
    elif style_name in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
        level = int(style_name[1]) + 1  # h1 → ## (since title is #)
        prefix = '#' * level + ' '
    elif style_name == 'PageTitle':
        return None  # Already in frontmatter

    # Handle image
    if image is not None:
        if skip_images:
            return f'{prefix}{text} *(image omitted)*' if text else None
        img_md = _convert_image(image, ns, images)
        if img_md:
            if text:
                return f'{prefix}{text}\n\n{img_md}'
            return img_md

    # Skip empty lines that are just formatting artifacts
    if not text and not prefix:
        return ''

    return f'{prefix}{text}'


def _convert_cdata_html(html_text: str) -> str:
    """Convert HTML-formatted CDATA text to markdown."""
    if not html_text or html_text.isspace():
        return ''

    if '<' not in html_text:
        return html_mod.unescape(html_text)

    soup = BeautifulSoup(html_text, 'html.parser')
    result = _process_html_node(soup)
    return html_mod.unescape(result)


def _process_html_node(node) -> str:
    """Recursively process HTML nodes to markdown."""
    if isinstance(node, str):
        return node

    if not hasattr(node, 'children'):
        return node.get_text() if hasattr(node, 'get_text') else str(node)

    if node.name == 'a':
        href = node.get('href', '')
        text = node.get_text()
        if href:
            return f'[{text}]({href})'
        return text

    if node.name == 'span':
        style = node.get('style', '')
        text = ''.join(_process_html_node(child) for child in node.children)
        if 'font-weight:bold' in style or 'font-weight: bold' in style:
            text = f'**{text}**'
        if 'font-style:italic' in style or 'font-style: italic' in style:
            text = f'*{text}*'
        if 'text-decoration:line-through' in style:
            text = f'~~{text}~~'
        return text

    if node.name == 'br':
        return '\n'

    # Default: process children
    return ''.join(_process_html_node(child) for child in node.children)


def _extract_cdata_text(html_text: str) -> str:
    """Extract plain text from CDATA HTML."""
    if '<' not in html_text:
        return html_text
    soup = BeautifulSoup(html_text, 'html.parser')
    return soup.get_text()


def _convert_image(image_elem, ns, images: dict) -> str:
    """Extract image data and return markdown reference."""
    data_elem = image_elem.find('one:Data', ns)
    if data_elem is None or not data_elem.text:
        return ''

    try:
        image_data = base64.b64decode(data_elem.text.strip())
    except Exception:
        return ''

    content_hash = hashlib.md5(image_data).hexdigest()[:12]
    filename = f'{content_hash}.png'
    images[filename] = image_data

    alt = image_elem.get('alt', '').strip()
    # Clean up auto-generated alt text
    alt = re.sub(r'^Computergenerierter Alternativtext:\s*', '', alt)
    alt = alt.split('\n')[0][:80]

    return f'![[{ATTACHMENTS_FOLDER}/{filename}]]'


def _convert_table(table_elem, ns) -> str:
    """Convert OneNote table to markdown table."""
    rows = table_elem.findall('one:Row', ns)
    if not rows:
        return ''

    has_header = table_elem.get('hasHeaderRow', 'false') == 'true'
    md_rows = []

    for row in rows:
        cells = row.findall('one:Cell', ns)
        cell_texts = []
        for cell in cells:
            texts = []
            for t in cell.findall('.//one:T', ns):
                if t.text:
                    texts.append(_convert_cdata_html(t.text))
            cell_text = ' '.join(texts).replace('|', '\\|').replace('\n', ' ')
            cell_texts.append(cell_text)
        md_rows.append('| ' + ' | '.join(cell_texts) + ' |')

    if md_rows:
        num_cols = md_rows[0].count('|') - 1
        separator = '| ' + ' | '.join(['---'] * max(num_cols, 1)) + ' |'
        insert_pos = 1 if has_header else 0
        md_rows.insert(insert_pos, separator)

    return '\n'.join(md_rows)


# ============================================================================
# File Path Utilities
# ============================================================================

def sanitize_filename(name: str) -> str:
    """Remove characters invalid for file paths on Windows."""
    invalid_chars = r'<>:"/\|?*'
    result = name
    for ch in invalid_chars:
        result = result.replace(ch, '_')
    result = result.rstrip('. ')
    result = re.sub(r'_+', '_', result)
    if len(result) > 100:
        result = result[:100]
    return result or 'Untitled'


def compute_output_path(page_info: dict, vault_mode: str) -> str:
    """Compute relative output path for a page."""
    section_path = page_info['section_path']
    parts = section_path.split('/')
    safe_parts = [sanitize_filename(p) for p in parts]

    if vault_mode == 'multi':
        safe_parts = safe_parts[1:]  # Remove notebook name (it becomes vault root)

    page_name = sanitize_filename(page_info['name'])
    return '/'.join(safe_parts + [page_name + '.md'])


# ============================================================================
# Sync Logic
# ============================================================================

def determine_actions(state: dict, pages: list[dict], args) -> list[dict]:
    """Compare state against current pages, return list of sync actions."""
    actions = []
    current_keys = set()

    for page in pages:
        key = page_key(page['id'])
        current_keys.add(key)

        entry = state['pages'].get(key)

        if entry is None:
            actions.append({'type': 'new', 'page': page, 'key': key})
            continue

        onenote_changed = (page['modified'] != entry.get('onenote_modified'))

        if not onenote_changed:
            continue  # Nothing to do — even if Obsidian changed, we don't overwrite OneNote

        # OneNote changed — check Obsidian side
        active_path = Path(args.output_dir) / entry['active_file']

        if not active_path.exists():
            if args.force_reexport or entry.get('status') != 'user_deleted':
                actions.append({'type': 'reexport', 'page': page, 'key': key, 'entry': entry})
            continue

        current_hash = file_hash(active_path)
        obsidian_changed = (current_hash != entry.get('content_hash', ''))

        if not obsidian_changed:
            actions.append({'type': 'update', 'page': page, 'key': key, 'entry': entry})
        else:
            actions.append({'type': 'conflict', 'page': page, 'key': key, 'entry': entry})

    # Check for orphaned pages (in state but not in current OneNote)
    for key in state['pages']:
        if key not in current_keys:
            entry = state['pages'][key]
            if entry.get('status') != 'orphaned':
                actions.append({'type': 'orphan', 'key': key, 'entry': entry})

    return actions


def print_dry_run(actions: list[dict], state: dict):
    """Print summary of what would happen."""
    by_type = {}
    for action in actions:
        by_type.setdefault(action['type'], []).append(action)

    print('\n' + '=' * 60)
    print('OneNote -> Obsidian Sync (DRY RUN)')
    print('=' * 60)
    if state.get('last_sync'):
        print(f'Last sync: {state["last_sync"]}')
    print()

    type_labels = {
        'new': ('NEW', '[N]'),
        'update': ('UPDATE (OneNote changed)', '[U]'),
        'conflict': ('CONFLICT (both changed)', '[C]'),
        'reexport': ('RE-EXPORT', '[R]'),
        'orphan': ('ORPHANED (deleted from OneNote)', '[O]'),
    }

    total_skip = len(state.get('pages', {})) - sum(
        len(v) for k, v in by_type.items() if k != 'new'
    )

    for action_type, (label, marker) in type_labels.items():
        items = by_type.get(action_type, [])
        if items:
            safe_print(f'{label}: {len(items)} pages')
            for item in items[:10]:
                page = item.get('page', {})
                entry = item.get('entry', {})
                path = page.get('section_path', entry.get('onenote_path', ''))
                name = page.get('name', entry.get('page_name', '?'))
                safe_print(f'  {marker} {path}/{name}')
            if len(items) > 10:
                safe_print(f'  ... ({len(items) - 10} more)')
            print()

    if total_skip > 0:
        print(f'SKIPPED (unchanged): {total_skip} pages')
    print(f'\nSummary: {len(actions)} actions pending')


def execute_actions(actions: list[dict], state: dict, args, temp_dir: Path):
    """Execute sync actions: export pages, write files, update state."""
    if not actions:
        print('  Nothing to sync — all pages up to date.')
        return

    # Track used file paths to handle duplicates
    used_paths = set()

    # Collect page IDs that need content export
    pages_to_export = [
        a['page'] for a in actions if a['type'] in ('new', 'update', 'conflict', 'reexport')
    ]

    if pages_to_export:
        print(f'\n  Fetching {len(pages_to_export)} page(s) from OneNote...')
        page_ids = [p['id'] for p in pages_to_export]
        errors = run_powershell_export(temp_dir, page_ids)
        if errors:
            print(f'  Warnings: {len(errors)} page(s) had export errors.')
            for err in errors[:5]:
                print(f'    {err[:100]}')

    # Process each action
    for i, action in enumerate(actions):
        atype = action['type']
        key = action['key']
        page = action.get('page', {})

        if atype == 'orphan':
            entry = action['entry']
            entry['status'] = 'orphaned'
            print(f'  [O] Orphaned: {entry.get("page_name", "?")}')
            continue

        # Compute output path
        rel_path = compute_output_path(page, args.vault_mode)

        if args.vault_mode == 'multi':
            nb_name = sanitize_filename(page['section_path'].split('/')[0])
            out_dir = Path(args.output_dir) / nb_name
        else:
            out_dir = Path(args.output_dir)

        full_path = out_dir / rel_path

        # Handle duplicate filenames
        if rel_path in used_paths:
            base = rel_path.rsplit('.md', 1)[0]
            counter = 2
            while f'{base} ({counter}).md' in used_paths:
                counter += 1
            rel_path = f'{base} ({counter}).md'
            full_path = out_dir / rel_path
        used_paths.add(rel_path)

        if atype == 'conflict':
            _handle_conflict(action, state, args, temp_dir, full_path, out_dir, rel_path)
        elif atype in ('new', 'update', 'reexport'):
            _handle_export(action, state, args, temp_dir, full_path, out_dir, rel_path)

        safe_print(f'\r  [{atype[0].upper()}] {i+1}/{len(actions)} {page.get("name", "")[:50]:<50}',
                   end='', flush=True)

    print()


def _handle_export(action, state, args, temp_dir, full_path, out_dir, rel_path):
    """Export or update a page."""
    page = action['page']
    key = action['key']

    xml_path = temp_dir / f'{page_key(page["id"])}.xml'
    if not xml_path.exists():
        return

    markdown, images = convert_page_xml(xml_path, page, args.skip_images)

    # Write markdown file
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(markdown, encoding='utf-8')

    # Write images
    if images:
        att_dir = full_path.parent / ATTACHMENTS_FOLDER
        att_dir.mkdir(exist_ok=True)
        for img_name, img_data in images.items():
            (att_dir / img_name).write_bytes(img_data)

    # Update state
    state['pages'][key] = {
        'onenote_id': page['id'],
        'onenote_path': page['section_path'],
        'page_name': page['name'],
        'onenote_modified': page['modified'],
        'active_file': rel_path,
        'content_hash': file_hash(full_path),
        'status': 'synced',
    }


def _handle_conflict(action, state, args, temp_dir, full_path, out_dir, rel_path):
    """Handle conflict: write OneNote version as conflict file, keep Obsidian version."""
    page = action['page']
    key = action['key']
    entry = action['entry']

    xml_path = temp_dir / f'{page_key(page["id"])}.xml'
    if not xml_path.exists():
        return

    markdown, images = convert_page_xml(xml_path, page, args.skip_images)

    # Generate conflict filename
    today = datetime.now().strftime('%Y-%m-%d')
    base_name = sanitize_filename(page['name'])
    conflict_name = f'{base_name} (OneNote conflict {today}).md'
    conflict_path = full_path.parent / conflict_name

    # Handle same-day repeat conflicts
    counter = 2
    while conflict_path.exists():
        conflict_name = f'{base_name} (OneNote conflict {today} #{counter}).md'
        conflict_path = full_path.parent / conflict_name
        counter += 1

    # Add conflict notice to frontmatter
    conflict_header = (
        f'> [!warning] OneNote Conflict\n'
        f'> This file was exported from OneNote because both OneNote and your local\n'
        f'> file changed since the last sync. Your edits are preserved in:\n'
        f'> `{full_path.name}`\n\n'
    )
    # Insert after frontmatter
    if markdown.startswith('---'):
        end_idx = markdown.index('---', 3) + 3
        markdown = markdown[:end_idx] + '\n\n' + conflict_header + markdown[end_idx:]
    else:
        markdown = conflict_header + markdown

    # Write conflict file
    full_path.parent.mkdir(parents=True, exist_ok=True)
    conflict_path.write_text(markdown, encoding='utf-8')

    # Write images for conflict file
    if images:
        att_dir = full_path.parent / ATTACHMENTS_FOLDER
        att_dir.mkdir(exist_ok=True)
        for img_name, img_data in images.items():
            (att_dir / img_name).write_bytes(img_data)

    # Update state: keep active_file pointing to user's file, update timestamps
    active_path = Path(args.output_dir) / entry['active_file']
    state['pages'][key] = {
        'onenote_id': page['id'],
        'onenote_path': page['section_path'],
        'page_name': page['name'],
        'onenote_modified': page['modified'],
        'active_file': entry['active_file'],
        'content_hash': file_hash(active_path),
        'status': 'conflict',
    }


# ============================================================================
# Main
# ============================================================================

def main():
    args = parse_args()

    print('=' * 60)
    print('  OneNote to Obsidian Sync')
    print('=' * 60)

    # Load existing sync state
    args.output_dir.mkdir(parents=True, exist_ok=True)
    state = load_sync_state(args.output_dir)
    is_first_run = state.get('last_sync') is None

    if is_first_run:
        print(f'\n  First run — full export to: {args.output_dir}')
    else:
        print(f'\n  Incremental sync (last: {state["last_sync"][:19]})')

    # Create temp directory for export
    temp_dir = Path(tempfile.mkdtemp(prefix='onenote_sync_'))
    errors = []

    try:
        # Phase 1: Get hierarchy (always needed)
        print('\n[Phase 1] Exporting from OneNote via COM...')

        if is_first_run and not args.dry_run:
            # Full export: get hierarchy + all pages in one go
            errors = run_powershell_export(temp_dir, page_ids=None)
        else:
            # Incremental or dry-run: only get hierarchy first
            hierarchy_script = f'''[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$onenote = New-Object -ComObject OneNote.Application
[string]$hierarchy = ""
$onenote.GetHierarchy("", 4, [ref]$hierarchy)
[System.IO.File]::WriteAllText("{str(temp_dir).replace(chr(92), '/')}/hierarchy.xml", $hierarchy, [System.Text.Encoding]::UTF8)
Write-Output "DONE"
'''
            script_path = temp_dir / 'hierarchy_only.ps1'
            script_path.write_text(hierarchy_script, encoding='utf-8')
            result = subprocess.run(
                ['powershell', '-ExecutionPolicy', 'Bypass', '-File', str(script_path)],
                capture_output=True, text=True, encoding='utf-8', errors='replace'
            )
            print('  Hierarchy exported.')
            errors = []

        # Phase 2: Parse hierarchy
        print('\n[Phase 2] Parsing notebook structure...')
        hierarchy_path = temp_dir / 'hierarchy.xml'
        notebooks = parse_hierarchy(hierarchy_path)

        all_pages = collect_all_pages(notebooks, args.notebooks)
        print(f'  Found {len(all_pages)} pages across {len(notebooks)} notebooks.')

        if args.notebooks:
            print(f'  Filtered to notebooks: {", ".join(args.notebooks)}')

        # Phase 3: Determine and execute actions
        print('\n[Phase 3] Syncing...')

        if is_first_run:
            # First run: all pages are "new"
            actions = [{'type': 'new', 'page': p, 'key': page_key(p['id'])} for p in all_pages]
        else:
            actions = determine_actions(state, all_pages, args)

        if args.dry_run:
            print_dry_run(actions, state)
        else:
            execute_actions(actions, state, args, temp_dir)
            save_sync_state(state, args.output_dir)

            # Summary
            by_type = {}
            for a in actions:
                by_type[a['type']] = by_type.get(a['type'], 0) + 1
            total = len(state.get('pages', {}))
            print(f'\n  Sync complete. {total} pages tracked.')
            if by_type:
                parts = [f'{v} {k}' for k, v in by_type.items()]
                print(f'  Actions: {", ".join(parts)}')

    finally:
        # Cleanup temp directory
        import shutil
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass

    if errors:
        print(f'\n  Note: {len(errors)} page(s) had export errors (see above).')

    print('\nDone!')


if __name__ == '__main__':
    main()

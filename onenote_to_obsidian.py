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
import re
import subprocess
import sys
import tempfile
import defusedxml.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import html

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
    parser.add_argument(
        '--force-reconvert', choices=['onenote', 'obsidian'], default=None,
        help='Re-convert all pages. "onenote" overwrites local files with fresh export; '
             '"obsidian" keeps local files but updates sync state to match them'
    )
    parser.add_argument(
        '--vision-ai', action='store_true',
        help='Enable Vision AI analysis of embedded images and PDFs. '
             'Creates AI summary notes in _ai_notes/ folders linked from the parent page.'
    )
    parser.add_argument(
        '--vision-ai-force', action='store_true',
        help='Re-run Vision AI analysis even for previously analyzed attachments'
    )
    parser.add_argument(
        '--ai-tags', action='store_true',
        help='Use Claude AI to generate semantic topic tags for each substantive page'
    )
    parser.add_argument(
        '--ai-tags-force', action='store_true',
        help='Re-tag pages even if content has not changed since last tagging'
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
    # Use single-quoted string in PowerShell (no variable/subexpression expansion)
    temp_dir_ps = str(temp_dir).replace("'", "''")
    temp_dir_escaped = str(temp_dir).replace('\\', '\\\\')

    if page_ids:
        page_id_list = '\n'.join(f'    "{pid}"' for pid in page_ids)
        page_export_block = f'''
$pageIds = @(
{page_id_list}
)
$count = 0
foreach ($pageId in $pageIds) {{
    $count++
    try {{
        [string]$pageXml = ""
        $onenote.GetPageContent($pageId, [ref]$pageXml, 1)
        $hash = [System.BitConverter]::ToString(
            [System.Security.Cryptography.SHA256]::Create().ComputeHash(
                [System.Text.Encoding]::UTF8.GetBytes($pageId)
            )
        ).Replace("-","").Substring(0,16).ToLower()
        $filename = "$exportDir\\$hash.xml"
        [System.IO.File]::WriteAllText($filename, $pageXml, [System.Text.Encoding]::UTF8)
        Write-Output "PROGRESS:$count/$($pageIds.Count)"
    }} catch {{
        Write-Output "ERROR:$pageId`:$($_.Exception.Message)"
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
        $filename = "$exportDir\\$hash.xml"
        [System.IO.File]::WriteAllText($filename, $pageXml, [System.Text.Encoding]::UTF8)
        Write-Output "PROGRESS:$count/$($pages.Count):$($page.name)"
    } catch {
        Write-Output "ERROR:$($page.ID):$($_.Exception.Message)"
    }
}
'''

    return f'''[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$onenote = New-Object -ComObject OneNote.Application
$exportDir = '{temp_dir_ps}'

# Export hierarchy
[string]$hierarchy = ""
$onenote.GetHierarchy("", 4, [ref]$hierarchy)
[System.IO.File]::WriteAllText("$exportDir\\hierarchy.xml", $hierarchy, [System.Text.Encoding]::UTF8)
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
        # Redact user paths from error output
        stderr_safe = re.sub(r'[A-Za-z]:\\Users\\[^\\]+', r'C:\\Users\\<USER>', stderr)
        safe_print(f'  PowerShell warnings: {stderr_safe[:200]}', file=sys.stderr)

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
        parent_stack = []  # stack of (name, index_in_pages)
        for page in sec['pages']:
            level = page['level']
            while len(parent_stack) >= level:
                parent_stack.pop()
            parent_name = parent_stack[-1][0] if parent_stack else None
            parent_idx = parent_stack[-1][1] if parent_stack else None

            page_entry = {
                **page,
                'section_path': sec_path,
                'parent_page': parent_name,
                'children': [],
            }
            current_idx = len(pages)
            pages.append(page_entry)

            # Register as child of parent
            if parent_idx is not None:
                pages[parent_idx]['children'].append(page['name'])

            parent_stack.append((page['name'], current_idx))


# ============================================================================
# Markdown Conversion
# ============================================================================

def _extract_authorship(root, ns) -> dict:
    """Extract author, contributors, and last_modified_by from page XML.

    Returns dict with keys: author, contributors, last_modified_by, last_modified_at
    """
    # Collect all (author, creationTime) and (lastModifiedBy, lastModifiedTime) from OE elements
    authors_with_time = []  # (name, timestamp)
    modifiers_with_time = []  # (name, timestamp)

    for oe in root.iter(f'{{{ns["one"]}}}OE'):
        author = oe.get('author')
        created = oe.get('creationTime', '')
        if author:
            authors_with_time.append((author, created))

        modifier = oe.get('lastModifiedBy')
        modified = oe.get('lastModifiedTime', '')
        if modifier:
            modifiers_with_time.append((modifier, modified))

    # Page-level lastModifiedBy (most authoritative for "last editor")
    page_modifier = root.get('lastModifiedBy', '')
    page_modified_time = root.get('lastModifiedTime', '')

    # Determine original author: the name associated with the earliest creationTime
    author = ''
    if authors_with_time:
        # Sort by timestamp, pick earliest
        with_times = [(name, ts) for name, ts in authors_with_time if ts]
        if with_times:
            with_times.sort(key=lambda x: x[1])
            author = with_times[0][0]
        else:
            author = authors_with_time[0][0]

    # Collect all unique contributor names (preserving first-seen order)
    seen = set()
    contributors = []
    for name, _ in authors_with_time + modifiers_with_time:
        if name and name not in seen:
            seen.add(name)
            contributors.append(name)

    # Last modified by
    last_modified_by = page_modifier or (modifiers_with_time[-1][0] if modifiers_with_time else '')
    last_modified_at = page_modified_time or (modifiers_with_time[-1][1] if modifiers_with_time else '')

    return {
        'author': author,
        'contributors': contributors,
        'last_modified_by': last_modified_by,
        'last_modified_at': last_modified_at,
    }


def _extract_tags(root, ns) -> list[str]:
    """Extract OneNote tags from page XML as a list of tag names.

    OneNote uses <one:TagDef> to define tag index→name mappings (e.g. "To Do", "Important"),
    and <one:Tag> on OE elements to reference them by index. The built-in "To Do" tags
    are handled as checkboxes already, so we exclude index 0 (typically the checkbox tag).
    """
    # Build tag index → name map from TagDef elements
    tag_defs = {}
    for tag_def in root.findall('one:TagDef', ns):
        idx = tag_def.get('index')
        name = tag_def.get('name', '')
        tag_type = tag_def.get('type', '')
        if idx and name:
            # Skip the To-Do/checkbox tag (type=0 or name "To Do") — already rendered as checkboxes
            if tag_type == '0' or name.lower() == 'to do':
                continue
            tag_defs[idx] = name

    if not tag_defs:
        return []

    # Collect all tags used on OE elements
    used_tags = set()
    for tag_elem in root.iter(f'{{{ns["one"]}}}Tag'):
        idx = tag_elem.get('index')
        if idx in tag_defs:
            used_tags.add(tag_defs[idx])

    # Convert to Obsidian-friendly tag format (lowercase, spaces→hyphens)
    result = []
    for tag_name in sorted(used_tags):
        slug = tag_name.lower().replace(' ', '-')
        # Remove characters not valid in Obsidian tags
        slug = re.sub(r'[^a-z0-9_/\-]', '', slug)
        if slug:
            result.append(slug)

    return result


def convert_page_xml(xml_path: Path, page_info: dict, skip_images: bool = False,
                     page_id_map: dict | None = None) -> tuple[str, dict]:
    """Convert OneNote page XML to Markdown string + dict of images."""
    global _current_page_id_map, _current_checkbox_tag_indices
    _current_page_id_map = page_id_map

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

    # Identify which tag indices are checkboxes (To-Do)
    _current_checkbox_tag_indices = set()
    for tag_def in root.findall('one:TagDef', ns):
        tag_type = tag_def.get('type', '')
        tag_name = tag_def.get('name', '')
        if tag_type == '0' or tag_name.lower() == 'to do':
            idx = tag_def.get('index')
            if idx:
                _current_checkbox_tag_indices.add(idx)

    # Get title
    title = page_info['name']
    title_elem = root.find('.//one:Title//one:T', ns)
    if title_elem is not None and title_elem.text:
        title = _extract_cdata_text(title_elem.text)

    # Extract authorship info
    authorship = _extract_authorship(root, ns)

    # Build frontmatter
    lines = ['---']
    lines.append(f'title: "{title.replace(chr(34), chr(39))}"')
    if authorship['author']:
        lines.append(f'author: "{authorship["author"]}"')
    if authorship['contributors'] and len(authorship['contributors']) > 1:
        lines.append('contributors:')
        for contributor in authorship['contributors']:
            lines.append(f'  - "{contributor}"')
    if authorship['last_modified_by']:
        lines.append(f'last_modified_by: "{authorship["last_modified_by"]}"')
    if authorship['last_modified_at']:
        lines.append(f'last_modified_at: {authorship["last_modified_at"]}')
    if page_info.get('created'):
        lines.append(f'created: {page_info["created"]}')
    if page_info.get('modified'):
        lines.append(f'modified: {page_info["modified"]}')
    lines.append('source: OneNote')

    # Notebook and section from section_path (Feature 3)
    section_path = page_info.get('section_path', '')
    if section_path:
        parts = section_path.strip('/').split('/')
        if parts:
            lines.append(f'notebook: "{parts[0]}"')
        if len(parts) > 1:
            lines.append(f'section: "{"/".join(parts[1:])}"')

    if page_info.get('parent_page'):
        safe_parent = page_info['parent_page'].replace('"', "'")
        lines.append(f'parent: "[[{safe_parent}]]"')

    # Children backlinks (Feature 4)
    if page_info.get('children'):
        lines.append('children:')
        for child_name in page_info['children']:
            safe_child = child_name.replace('"', "'")
            lines.append(f'  - "[[{safe_child}]]"')

    # Tags from OneNote (Feature 1)
    page_tags = _extract_tags(root, ns)
    if page_tags:
        lines.append('tags:')
        for tag in page_tags:
            lines.append(f'  - "{tag}"')

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
    _current_page_id_map = None
    _current_checkbox_tag_indices = set()
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

    # Check for tag (only render as checkbox if it's a To-Do/checkbox tag)
    tag = oe_elem.find('one:Tag', ns)
    is_checkbox_tag = (tag is not None and
                       tag.get('index', '') in _current_checkbox_tag_indices)

    # Determine style/heading
    style_idx = oe_elem.get('quickStyleIndex', '')
    style_name = style_map.get(style_idx, 'p')

    # Build the line
    prefix = ''
    indent_str = '  ' * indent

    if is_checkbox_tag:
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

    # Handle embedded file (PDF, Word, Excel, etc.)
    inserted_file = oe_elem.find('one:InsertedFile', ns)
    if inserted_file is not None:
        file_md = _convert_inserted_file(inserted_file, ns, images)
        if file_md:
            if text:
                return f'{prefix}{text}\n{file_md}'
            return f'{prefix}{file_md}'

    # Skip empty lines that are just formatting artifacts
    if not text and not prefix:
        return ''

    return f'{prefix}{text}'


_MD_ESCAPE_RE = re.compile(r'([\\`*_\[\]~#])')
_MD_LINE_START_RE = re.compile(r'^(#{1,6}\s|>)', re.MULTILINE)


def _escape_md_text(text: str) -> str:
    """Escape markdown-special characters in literal text from OneNote."""
    text = _MD_ESCAPE_RE.sub(r'\\\1', text)
    text = _MD_LINE_START_RE.sub(r'\\\1', text)
    return text


def _convert_cdata_html(html_text: str) -> str:
    """Convert HTML-formatted CDATA text to markdown."""
    if not html_text or html_text.isspace():
        return ''

    if '<' not in html_text:
        return _escape_md_text(html.unescape(html_text))

    soup = BeautifulSoup(html_text, 'html.parser')
    return _process_html_node(soup)


# Module-level state set during convert_page_xml
_current_page_id_map = None
_current_checkbox_tag_indices = set()  # Tag indices that are To-Do (checkbox) tags


def _resolve_onenote_link(href: str, text: str) -> str:
    """Resolve a onenote:// URL to an Obsidian [[wikilink]] if possible."""
    if not _current_page_id_map or not href.lower().startswith('onenote:'):
        return f'[{text}]({href})'

    # OneNote URLs contain page GUIDs in the fragment or path
    # Format: onenote:...&page-id={GUID}... or onenote:#SectionName&page-id={GUID}
    page_id_match = re.search(r'page-id=\{?([^}&]+)\}?', href, re.IGNORECASE)
    if page_id_match:
        page_id = page_id_match.group(1)
        # Try exact match and with braces
        for candidate in (page_id, f'{{{page_id}}}'):
            if candidate in _current_page_id_map:
                page_name = _current_page_id_map[candidate]
                return f'[[{page_name}]]'

    # Also try section-id for section links
    section_id_match = re.search(r'section-id=\{?([^}&]+)\}?', href, re.IGNORECASE)
    if section_id_match and not page_id_match:
        # Section-only link — keep as text with note
        return f'{text} *(OneNote section link)*'

    # Couldn't resolve — keep original link text with marker
    return f'{text} *(unresolved OneNote link)*'


def _process_html_node(node) -> str:
    """Recursively process HTML nodes to markdown."""
    if isinstance(node, str):
        return _escape_md_text(node)

    if not hasattr(node, 'children'):
        return node.get_text() if hasattr(node, 'get_text') else str(node)

    if node.name == 'a':
        href = node.get('href', '')
        text = node.get_text()
        if href:
            if href.lower().startswith('onenote:'):
                return _resolve_onenote_link(href, text)
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

    content_hash = hashlib.sha256(image_data).hexdigest()[:12]
    filename = f'{content_hash}.png'
    images[filename] = image_data

    return f'![[{ATTACHMENTS_FOLDER}/{filename}]]'


_ONENOTE_CACHE_DIRS = None


def _is_safe_cache_path(path_str: str) -> bool:
    """Validate that a cache path points to an allowed OneNote cache directory."""
    global _ONENOTE_CACHE_DIRS
    if _ONENOTE_CACHE_DIRS is None:
        home = Path.home()
        _ONENOTE_CACHE_DIRS = [
            home / 'AppData' / 'Local' / 'Microsoft' / 'OneNote',
            home / 'AppData' / 'Local' / 'Temp',
            home / 'AppData' / 'Local' / 'Packages',  # UWP OneNote
        ]
    try:
        resolved = Path(path_str).resolve()
        return any(
            resolved == allowed or allowed in resolved.parents
            for allowed in _ONENOTE_CACHE_DIRS
        )
    except (ValueError, OSError):
        return False


def _convert_inserted_file(file_elem, ns, images: dict) -> str:
    """Extract an embedded file and return markdown link."""
    data_elem = file_elem.find('one:Data', ns)
    file_data = None

    if data_elem is not None and data_elem.text:
        try:
            file_data = base64.b64decode(data_elem.text.strip())
        except Exception:
            pass

    if file_data is None:
        cache_path = file_elem.get('pathCache', '')
        if cache_path and _is_safe_cache_path(cache_path) and Path(cache_path).exists():
            try:
                file_data = Path(cache_path).read_bytes()
            except Exception:
                pass

    if file_data is None:
        return ''

    original_name = file_elem.get('preferredName') or file_elem.get('pathSource', '')
    if original_name:
        original_name = original_name.rsplit('\\', 1)[-1].rsplit('/', 1)[-1]

    if not original_name:
        content_hash = hashlib.sha256(file_data).hexdigest()[:12]
        original_name = f'{content_hash}.bin'

    safe_name = sanitize_filename(Path(original_name).stem)
    ext = Path(original_name).suffix.lower()
    content_hash = hashlib.sha256(file_data).hexdigest()[:8]
    filename = f'{safe_name}_{content_hash}{ext}'

    images[filename] = file_data
    if ext == '.pdf':
        return f'![[{ATTACHMENTS_FOLDER}/{filename}]]'
    return f'[[{ATTACHMENTS_FOLDER}/{filename}]]'


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

_WINDOWS_RESERVED = frozenset({
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
})


def sanitize_filename(name: str) -> str:
    """Remove characters invalid for file paths on Windows."""
    invalid_chars = r'<>:"/\|?*'
    result = name
    for ch in invalid_chars:
        result = result.replace(ch, '_')
    result = result.rstrip('. ')
    result = re.sub(r'_+', '_', result)
    # Block Windows reserved device names
    if result.split('.')[0].upper() in _WINDOWS_RESERVED:
        result = f'_{result}'
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
# Auto-Wikilinks & AI Tags
# ============================================================================

def _split_frontmatter(markdown: str) -> tuple[str, str]:
    """Split markdown into (frontmatter, body). Frontmatter includes the --- delimiters."""
    if not markdown.startswith('---'):
        return '', markdown
    end_idx = markdown.index('---', 3) + 3
    return markdown[:end_idx], markdown[end_idx:]


def _auto_wikify(markdown: str, all_page_names: set[str], current_page_name: str) -> str:
    """Replace mentions of other page names with [[wikilinks]] in body text."""
    frontmatter, body = _split_frontmatter(markdown)
    if not body.strip():
        return markdown

    # Sort names longest-first to avoid partial matches
    candidates = sorted(
        (name for name in all_page_names
         if name != current_page_name and len(name) > 3),
        key=len, reverse=True
    )

    if not candidates:
        return markdown

    # Split body into protected and unprotected segments
    # Protected: code blocks (``` ... ```), inline code (` ... `),
    #            existing wikilinks [[...]], markdown links [...](...)
    protected_pattern = re.compile(
        r'```[\s\S]*?```'       # fenced code blocks
        r'|`[^`\n]+`'          # inline code
        r'|\[\[[^\]]+\]\]'     # wikilinks
        r'|\[[^\]]*\]\([^)]*\)'  # markdown links
    )

    # Build segments: list of (text, is_protected)
    segments = []
    last_end = 0
    for m in protected_pattern.finditer(body):
        if m.start() > last_end:
            segments.append((body[last_end:m.start()], False))
        segments.append((m.group(), True))
        last_end = m.end()
    if last_end < len(body):
        segments.append((body[last_end:], False))

    # Replace in unprotected segments
    for name in candidates:
        pattern = re.compile(r'\b' + re.escape(name) + r'\b', re.IGNORECASE)
        new_segments = []
        for text, protected in segments:
            if protected:
                new_segments.append((text, True))
            else:
                # Replace matches but preserve original case in the link display
                def _make_link(match):
                    return f'[[{name}]]'
                new_text = pattern.sub(_make_link, text)
                new_segments.append((new_text, False))
        segments = new_segments

    new_body = ''.join(text for text, _ in segments)
    return frontmatter + new_body


def _inject_ai_tags(markdown: str, ai_tags: list[str]) -> str:
    """Merge AI-generated tags into the frontmatter tags: field."""
    if not ai_tags:
        return markdown

    frontmatter, body = _split_frontmatter(markdown)
    if not frontmatter:
        return markdown

    lines = frontmatter.split('\n')

    # Find existing tags section
    tags_start = None
    tags_end = None
    existing_tags = set()

    for i, line in enumerate(lines):
        if line.strip() == 'tags:':
            tags_start = i
        elif tags_start is not None and line.startswith('  - '):
            # Extract tag value: "  - "value"" or "  - value"
            tag_val = line.strip().removeprefix('- ').strip('"').strip("'")
            existing_tags.add(tag_val)
            tags_end = i
        elif tags_start is not None and not line.startswith('  - '):
            break

    # Deduplicate: only add tags not already present
    new_tags = [t for t in ai_tags if t not in existing_tags]
    if not new_tags:
        return markdown

    if tags_start is not None:
        # Insert after existing tags
        insert_pos = (tags_end + 1) if tags_end is not None else (tags_start + 1)
        for tag in reversed(new_tags):
            lines.insert(insert_pos, f'  - "{tag}"')
    else:
        # Insert tags: section before the closing ---
        closing_idx = len(lines) - 1
        while closing_idx > 0 and lines[closing_idx].strip() != '---':
            closing_idx -= 1
        tag_lines = ['tags:'] + [f'  - "{tag}"' for tag in sorted(existing_tags | set(new_tags))]
        for tl in reversed(tag_lines):
            lines.insert(closing_idx, tl)

    new_frontmatter = '\n'.join(lines)
    return new_frontmatter + body


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
        'keep_obsidian': ('KEEP OBSIDIAN (update state only)', '[K]'),
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


def execute_actions(actions: list[dict], state: dict, args, temp_dir: Path,
                    page_id_map: dict | None = None, all_page_names: set[str] | None = None):
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

        if atype == 'keep_obsidian':
            _handle_keep_obsidian(action, state, args, full_path, rel_path)
        elif atype == 'conflict':
            _handle_conflict(action, state, args, temp_dir, full_path, out_dir, rel_path,
                             page_id_map, all_page_names)
        elif atype in ('new', 'update', 'reexport'):
            _handle_export(action, state, args, temp_dir, full_path, out_dir, rel_path,
                           page_id_map, all_page_names)

        safe_print(f'\r  [{atype[0].upper()}] {i+1}/{len(actions)} {page.get("name", "")[:50]:<50}',
                   end='', flush=True)

    print()


def _handle_keep_obsidian(action, state, args, full_path, rel_path):
    """Keep the Obsidian file as-is but update sync state to match it."""
    page = action['page']
    key = action['key']

    state['pages'][key] = {
        'onenote_id': page['id'],
        'onenote_path': page['section_path'],
        'page_name': page['name'],
        'onenote_modified': page['modified'],
        'active_file': rel_path,
        'content_hash': file_hash(full_path) if full_path.exists() else '',
        'status': 'synced',
    }


def _parse_embed_order(markdown: str) -> list[dict]:
    """Parse the embed order from generated markdown for Vision AI context."""
    embed_order = []
    current_text = []
    lines = markdown.split('\n')

    # Skip YAML frontmatter block if present
    start = 0
    if lines and lines[0].strip() == '---':
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                start = i + 1
                break

    for line in lines[start:]:
        stripped = line.strip()
        # Match ![[_attachments/filename]] or [[_attachments/filename]]
        embed_match = re.match(r'!?\[\[_attachments/(.+?)\]\]', stripped)
        if embed_match:
            if current_text:
                text = '\n'.join(current_text).strip()
                if text:
                    embed_order.append({'type': 'text', 'filename': '', 'text': text})
                current_text = []
            filename = embed_match.group(1)
            embed_order.append({'type': 'image' if stripped.startswith('!') else 'file',
                                'filename': filename, 'text': ''})
        elif stripped.startswith('#'):
            if current_text:
                text = '\n'.join(current_text).strip()
                if text:
                    embed_order.append({'type': 'text', 'filename': '', 'text': text})
                current_text = []
            embed_order.append({'type': 'text', 'filename': '', 'text': stripped})
        elif stripped:
            current_text.append(stripped)

    if current_text:
        text = '\n'.join(current_text).strip()
        if text:
            embed_order.append({'type': 'text', 'filename': '', 'text': text})

    return embed_order


def _handle_export(action, state, args, temp_dir, full_path, out_dir, rel_path,
                   page_id_map=None, all_page_names=None):
    """Export or update a page."""
    page = action['page']
    key = action['key']

    xml_path = temp_dir / f'{page_key(page["id"])}.xml'
    if not xml_path.exists():
        return

    markdown, images = convert_page_xml(xml_path, page, args.skip_images, page_id_map)

    # AI semantic tags (before wikilinks so tags don't get wikified)
    if args.ai_tags and not args.dry_run:
        try:
            from vision_ai.tagger import generate_tags
            ai_tags = generate_tags(
                markdown_body=markdown,
                page_title=page['name'],
                section_path=page.get('section_path', ''),
                existing_tags=[],
                state=state,
                page_key=key,
                force=args.ai_tags_force,
            )
            if ai_tags:
                markdown = _inject_ai_tags(markdown, ai_tags)
        except ImportError as e:
            print(f'\n  [!] AI tags dependencies not available: {e}')
        except Exception as e:
            print(f'\n  [!] AI tags failed for {page["name"]}: {e}')

    # Auto-wikilinks
    if all_page_names:
        markdown = _auto_wikify(markdown, all_page_names, page['name'])

    # Write markdown file
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(markdown, encoding='utf-8')

    # Write images
    if images:
        att_dir = full_path.parent / ATTACHMENTS_FOLDER
        att_dir.mkdir(exist_ok=True)
        for img_name, img_data in images.items():
            (att_dir / img_name).write_bytes(img_data)

    # Vision AI post-processing
    if args.vision_ai and images and not args.dry_run:
        try:
            from vision_ai import analyze_page_attachments
        except ImportError as e:
            print(f'\n  [!] Vision AI dependencies not installed: {e}')
            print('      Install with: pip install anthropic pymupdf pdfplumber python-pptx pandas tabulate')
        else:
            try:
                embed_order = _parse_embed_order(markdown)
                page_context = {
                    'page_name': page['name'],
                    'section_path': page.get('section_path', ''),
                    'markdown_text': markdown,
                    'parent_page': page.get('parent_page'),
                    'embed_order': embed_order,
                }
                analyze_page_attachments(
                    page_md_path=full_path,
                    attachments_dir=full_path.parent / ATTACHMENTS_FOLDER,
                    images=images,
                    page_context=page_context,
                    force=args.vision_ai_force,
                )
            except Exception as e:
                print(f'\n  [!] Vision AI failed for {page["name"]}: {e}')

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


def _handle_conflict(action, state, args, temp_dir, full_path, out_dir, rel_path,
                     page_id_map=None, all_page_names=None):
    """Handle conflict: write OneNote version as conflict file, keep Obsidian version."""
    page = action['page']
    key = action['key']
    entry = action['entry']

    xml_path = temp_dir / f'{page_key(page["id"])}.xml'
    if not xml_path.exists():
        return

    markdown, images = convert_page_xml(xml_path, page, args.skip_images, page_id_map)

    # Auto-wikilinks on conflict file too
    if all_page_names:
        markdown = _auto_wikify(markdown, all_page_names, page['name'])

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
            temp_dir_ps = str(temp_dir).replace("'", "''")
            hierarchy_script = f'''[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$onenote = New-Object -ComObject OneNote.Application
$exportDir = '{temp_dir_ps}'
[string]$hierarchy = ""
$onenote.GetHierarchy("", 4, [ref]$hierarchy)
[System.IO.File]::WriteAllText("$exportDir\\hierarchy.xml", $hierarchy, [System.Text.Encoding]::UTF8)
Write-Output "DONE"
'''
            script_path = temp_dir / 'hierarchy_only.ps1'
            script_path.write_text(hierarchy_script, encoding='utf-8')
            subprocess.run(
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

        if args.force_reconvert:
            if args.force_reconvert == 'obsidian':
                actions = [{'type': 'keep_obsidian', 'page': p, 'key': page_key(p['id'])}
                           for p in all_pages if page_key(p['id']) in state['pages']]
                new_pages = [p for p in all_pages if page_key(p['id']) not in state['pages']]
                actions += [{'type': 'new', 'page': p, 'key': page_key(p['id'])} for p in new_pages]
            else:
                actions = [{'type': 'update', 'page': p, 'key': page_key(p['id']),
                            'entry': state['pages'].get(page_key(p['id']), {})}
                           for p in all_pages if page_key(p['id']) in state['pages']]
                new_pages = [p for p in all_pages if page_key(p['id']) not in state['pages']]
                actions += [{'type': 'new', 'page': p, 'key': page_key(p['id'])} for p in new_pages]
            print(f'  Force reconvert (keep={args.force_reconvert}): {len(actions)} pages')
        elif is_first_run:
            # First run: all pages are "new"
            actions = [{'type': 'new', 'page': p, 'key': page_key(p['id'])} for p in all_pages]
        else:
            actions = determine_actions(state, all_pages, args)

        # Build page ID → name map for resolving onenote:// links
        page_id_map = {p['id']: p['name'] for p in all_pages}
        # Build set of all page names for auto-wikilinks
        all_page_names = {p['name'] for p in all_pages}

        if args.dry_run:
            print_dry_run(actions, state)
        else:
            execute_actions(actions, state, args, temp_dir, page_id_map, all_page_names)
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

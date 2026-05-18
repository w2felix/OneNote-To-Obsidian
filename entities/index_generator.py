"""Generate entity index pages for Obsidian cross-referencing.

Creates one markdown page per entity in _entity_index/{type}/{name}.md,
each containing a Dataview query that lists all pages mentioning that entity.
"""

import logging
import re
from pathlib import Path

from entities.dictionaries import load_dictionaries

logger = logging.getLogger(__name__)

ENTITY_INDEX_FOLDER = '_entity_index'


def generate_entity_index(output_dir: Path, state: dict):
    """Generate/update entity index pages from sync state entity data.

    Args:
        output_dir: Root of the Obsidian vault (obsidian_export/)
        state: Full sync state dict containing ai_entities
    """
    entities_state = state.get('ai_entities', {})
    if not entities_state:
        logger.info("No entity data in state, skipping index generation")
        return

    # Aggregate all entities across all pages
    all_entities: dict[str, dict[str, set]] = {
        'genes': {},
        'drugs': {},
        'diseases': {},
        'compounds': {},
        'companies': {},
        'roles': {},
        'methods': {},
        'clinical_trials': {},
        'cell_lines': {},
        'conferences': {},
        'pathways': {},
    }

    for page_key, page_data in entities_state.items():
        entities = page_data.get('entities', {})
        for entity_type in all_entities:
            for name in entities.get(entity_type, []):
                all_entities[entity_type].setdefault(name, set()).add(page_key)

    # Load dictionaries for metadata
    dicts = load_dictionaries()

    # Generate index pages
    index_dir = output_dir / ENTITY_INDEX_FOLDER
    total_written = 0

    for entity_type, entities in all_entities.items():
        type_dir = index_dir / entity_type
        type_dir.mkdir(parents=True, exist_ok=True)

        for name, page_keys in entities.items():
            filepath = type_dir / f"{_safe_filename(name)}.md"
            content = _build_entity_page(name, entity_type, len(page_keys), dicts)
            filepath.write_text(content, encoding='utf-8')
            total_written += 1

    logger.info(f"Entity index: wrote {total_written} pages in {index_dir}")
    print(f'  Entity index: {total_written} entity pages in {ENTITY_INDEX_FOLDER}/')


def _safe_filename(name: str) -> str:
    """Make a filename safe for all platforms."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    return safe.strip('. ')[:100] or 'unnamed'


def _build_entity_page(name: str, entity_type: str, mention_count: int,
                       dicts) -> str:
    """Build markdown content for a single entity index page."""
    lines = ['---']
    lines.append(f'title: "{name}"')
    lines.append(f'entity_type: {entity_type}')

    # Add ontology metadata
    if entity_type == 'genes' and name in dicts.gene_info:
        info = dicts.gene_info[name]
        lines.append(f'full_name: "{info.get("name", "")}"')
        lines.append(f'hgnc_id: "{info.get("hgnc_id", "")}"')
        aliases = info.get('aliases', [])
        if aliases:
            lines.append('aliases:')
            for a in aliases[:10]:
                lines.append(f'  - "{a}"')

    elif entity_type == 'diseases':
        disease_info = dicts.disease_names.get(name.lower(), {})
        if disease_info:
            mondo_id = disease_info.get('mondo_id', '')
            if mondo_id:
                lines.append(f'mondo_id: "{mondo_id}"')
            parents = disease_info.get('parents', [])
            if parents:
                lines.append('parent_diseases:')
                for p in parents[:5]:
                    lines.append(f'  - "{p}"')

    elif entity_type == 'compounds':
        compound_info = dicts.compounds.get(name, {})
        if compound_info:
            for k, v in compound_info.items():
                lines.append(f'{k}: "{v}"')

    lines.append(f'mention_count: {mention_count}')
    lines.append('auto_generated: true')
    lines.append('---')
    lines.append('')

    # Header
    lines.append(f'# {name}')
    lines.append('')

    # Subtitle with metadata
    if entity_type == 'genes' and name in dicts.gene_info:
        lines.append(f'*{dicts.gene_info[name].get("name", "")}*')
        lines.append('')
    elif entity_type == 'compounds':
        compound_info = dicts.compounds.get(name, {})
        if compound_info:
            parts = []
            if compound_info.get('name'):
                parts.append(compound_info['name'])
            if compound_info.get('target'):
                parts.append(f"Target: {compound_info['target']}")
            if compound_info.get('modality'):
                parts.append(f"({compound_info['modality']})")
            if parts:
                lines.append(f'*{" — ".join(parts)}*')
                lines.append('')

    # Dataview query
    lines.append('## References')
    lines.append('')
    lines.append('```dataview')
    lines.append('LIST')
    lines.append('FROM ""')
    lines.append(f'WHERE contains(entities.{entity_type}, "{name}")')
    lines.append('SORT file.mtime DESC')
    lines.append('```')

    return '\n'.join(lines)

"""Build entity lookup JSONs from downloaded ontology source files.

Input files (must be present in entity_data/):
  hgnc_complete_set.tsv  — HGNC gene nomenclature
  mondo.json             — MONDO disease ontology (JSON-LD)

Output files:
  hgnc_genes.json        — {genes: {symbol: {hgnc_id}}, alias_map: {alias: symbol}}
  mondo_diseases.json    — {lowercase_name: {label, mondo_id}}
"""

import csv
import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent


def build_hgnc(src: Path, out: Path):
    print(f"Building gene dictionary from {src.name}...")

    genes = {}
    alias_map = {}
    skipped = 0

    with open(src, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            if row.get('status', '').strip() != 'Approved':
                skipped += 1
                continue

            symbol = row.get('symbol', '').strip()
            hgnc_id = row.get('hgnc_id', '').strip()
            if not symbol:
                continue

            genes[symbol] = {'hgnc_id': hgnc_id}

            # Alias symbols (pipe-separated)
            for alias in row.get('alias_symbol', '').split('|'):
                alias = alias.strip()
                if alias and alias != symbol and alias not in genes:
                    alias_map[alias] = symbol

            # Previous symbols also treated as aliases
            for prev in row.get('prev_symbol', '').split('|'):
                prev = prev.strip()
                if prev and prev != symbol and prev not in genes:
                    alias_map[prev] = symbol

    data = {'genes': genes, 'alias_map': alias_map}
    out.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    print(f"  {len(genes):,} approved gene symbols, {len(alias_map):,} aliases "
          f"({skipped:,} non-approved skipped)")
    print(f"  Written: {out.name} ({out.stat().st_size // 1024:,} KB)")


# MONDO:0000001 is the root of actual diseases ("disease or disorder").
# Sibling branches like MONDO:0021125 (disease characteristic) contain modifiers
# such as "acquired", "congenital", "not rare", "restricted to specific location".
# Historical bug: build_mondo indexed every MONDO CLASS, so those modifiers
# ended up as valid disease labels and the downstream wikifier turned every
# occurrence of the word "acquired" in prose into [[acquired]].
_MONDO_DISEASE_ROOT = 'http://purl.obolibrary.org/obo/MONDO_0000001'

# Extra guard: common English words that occasionally appear as MONDO synonyms
# via edge cases (e.g. a description-derived synonym). Never accept these as
# diseases regardless of ancestry.
_DISEASE_KEY_STOPLIST = {
    'acquired', 'congenital', 'inherited', 'sporadic',
    'not rare', 'rare', 'common', 'not applicable',
    'restricted to specific location', 'systemic',
    'disease', 'condition', 'disorder', 'disease or disorder',
    'benign', 'malignant',
    'arms', 'read', 'age', 'mild', 'moderate', 'severe',
}


def _descendants_of(root: str, edges: list) -> set[str]:
    """Return the set of MONDO node URLs whose is_a ancestry includes `root`."""
    children: dict[str, list[str]] = {}
    for e in edges:
        if e.get('pred') == 'is_a':
            children.setdefault(e['obj'], []).append(e['sub'])

    seen: set[str] = {root}
    frontier = [root]
    while frontier:
        cur = frontier.pop()
        for child in children.get(cur, ()):
            if child not in seen:
                seen.add(child)
                frontier.append(child)
    return seen


def build_mondo(src: Path, out: Path):
    print(f"Building disease dictionary from {src.name}  (this may take ~30s)...")

    with open(src, encoding='utf-8') as f:
        mondo = json.load(f)

    # MONDO JSON-LD: graphs[0].nodes + graphs[0].edges
    graphs = mondo.get('graphs', [])
    if not graphs:
        print("ERROR: No 'graphs' key in mondo.json — wrong format?", file=sys.stderr)
        sys.exit(1)

    nodes = graphs[0].get('nodes', [])
    edges = graphs[0].get('edges', [])

    # Restrict to genuine diseases: nodes whose ancestry includes MONDO:0000001.
    disease_urls = _descendants_of(_MONDO_DISEASE_ROOT, edges)

    diseases = {}
    skipped_obsolete = 0
    skipped_off_branch = 0
    skipped_stoplist = 0

    for node in nodes:
        if node.get('type') != 'CLASS':
            continue

        node_id = node.get('id', '')
        if 'MONDO_' not in node_id:
            continue

        if node_id not in disease_urls:
            skipped_off_branch += 1
            continue

        meta = node.get('meta', {})

        # Skip obsolete terms
        if meta.get('deprecated', False):
            skipped_obsolete += 1
            continue

        label = node.get('lbl', '').strip()
        if not label or len(label) < 3:
            continue

        mondo_id = node_id.split('/')[-1].replace('_', ':')

        entry = {'label': label, 'mondo_id': mondo_id}

        # Register under lowercase label (unless it's a known-bad token).
        key = label.lower()
        if key in _DISEASE_KEY_STOPLIST:
            skipped_stoplist += 1
        elif key not in diseases:
            diseases[key] = entry

        # Register all synonyms with the same guards.
        for syn in meta.get('synonyms', []):
            val = syn.get('val', '').strip()
            if not val or len(val) < 4:
                continue
            syn_key = val.lower()
            if syn_key in _DISEASE_KEY_STOPLIST:
                skipped_stoplist += 1
                continue
            if syn_key not in diseases:
                diseases[syn_key] = entry

    out.write_text(json.dumps(diseases, ensure_ascii=False), encoding='utf-8')
    print(f"  {len(diseases):,} disease name/synonym entries "
          f"({skipped_obsolete:,} obsolete, {skipped_off_branch:,} off-branch, "
          f"{skipped_stoplist:,} stoplist)")
    print(f"  Written: {out.name} ({out.stat().st_size // 1024:,} KB)")


def main():
    hgnc_src = DATA_DIR / 'hgnc_complete_set.tsv'
    mondo_src = DATA_DIR / 'mondo.json'

    missing = [f.name for f in (hgnc_src, mondo_src) if not f.exists()]
    if missing:
        print(f"ERROR: Missing source files: {', '.join(missing)}", file=sys.stderr)
        print("Run setup_entities.py to download them.", file=sys.stderr)
        sys.exit(1)

    build_hgnc(hgnc_src, DATA_DIR / 'hgnc_genes.json')
    print()
    build_mondo(mondo_src, DATA_DIR / 'mondo_diseases.json')
    print("\nDone. Entity dictionaries are ready.")


if __name__ == '__main__':
    main()

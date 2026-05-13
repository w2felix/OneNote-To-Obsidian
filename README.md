# OneNote to Obsidian Sync

A Python script that exports your OneNote notebooks to Obsidian-compatible Markdown files — and keeps them in sync on subsequent runs. Optionally enriches pages with AI-generated analysis, semantic tags, and cross-page wikilinks.

Built for environments where the Obsidian OneNote plugin can't be used (e.g. corporate setups without admin/OAuth access).

## Requirements

- **Windows** with OneNote desktop app installed and logged in
- **Python 3.10+**

```bash
# Core dependencies
pip install defusedxml beautifulsoup4

# Optional: Vision AI + AI tags (only needed with --vision-ai or --ai-tags)
pip install anthropic pymupdf pillow pdfplumber python-pptx python-docx pandas tabulate openpyxl pytesseract
```

> The script uses PowerShell COM automation to talk to OneNote. No browser login or admin rights needed — if OneNote opens and shows your notebooks, you're good.

For Vision AI / AI tags, set the `ANTHROPIC_API_KEY` environment variable (or configure your proxy in `vision_ai/client.py`).

**Tesseract OCR** (optional) — needed for text extraction from screenshots. `pytesseract` (listed above) is the Python binding, but the Tesseract binary must also be installed separately. With conda: `conda install tesseract`. Without it, screenshot analysis still runs but skips OCR.

## Quick Start

```bash
# Export everything (first run = full export, subsequent runs = incremental sync)
python onenote_to_obsidian.py

# Full-featured run: sync + Vision AI + semantic tags
python onenote_to_obsidian.py --vision-ai --ai-tags
```

This creates an `obsidian_export/` folder you can open directly as an Obsidian vault.

## Options

### Basic

| Flag | Description | Default |
|------|-------------|---------|
| `--output-dir PATH` | Where to write the vault | `./obsidian_export` |
| `--notebooks "Name1" "Name2"` | Only sync specific notebooks (exact names as in OneNote) | All notebooks |
| `--vault-mode single\|multi` | `single` = all notebooks in one vault. `multi` = each notebook as a separate vault folder | `single` |
| `--skip-images` | Skip images and file attachments (text-only export) | Off |

### Sync Control

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview what would change without writing anything |
| `--force-reexport` | Re-export pages previously deleted from the vault |
| `--force-reconvert onenote` | Overwrite all local files with fresh OneNote export — **any manual edits in Obsidian will be lost** |
| `--force-reconvert obsidian` | Accept current vault as baseline without touching files |

### AI Features

| Flag | Description |
|------|-------------|
| `--vision-ai` | Analyze embedded images, PDFs, and data files with Claude Vision. Creates `_ai_notes/` with linked summaries |
| `--vision-ai-force` | Re-analyze attachments even if previously cached |
| `--ai-tags` | Generate 3-7 semantic topic tags per page using Claude. Tags are added to YAML frontmatter |
| `--ai-tags-force` | Re-tag pages even if content hasn't changed |
| `--entities` | Extract biomedical entities (genes, drugs, diseases, M-codes) into frontmatter |
| `--entities-force` | Re-extract entities even if content hasn't changed |
| `--entity-index` | Generate `_entity_index/` hub pages with Dataview cross-reference queries |

### Examples

```bash
# Only sync two notebooks
python onenote_to_obsidian.py --notebooks "Work" "Personal"

# Preview changes without writing
python onenote_to_obsidian.py --dry-run

# Re-export with improved conversion after script update
python onenote_to_obsidian.py --force-reconvert onenote

# Run Vision AI on a specific notebook
python onenote_to_obsidian.py --notebooks "Research" --vision-ai

# Generate semantic tags for all pages
python onenote_to_obsidian.py --ai-tags

# Full pipeline: sync + analyze attachments + tag pages
python onenote_to_obsidian.py --vision-ai --ai-tags

# Extract entities (fast, no API calls — local dictionary only)
python onenote_to_obsidian.py --entities --entity-index

# Full pipeline with entities (tags + entities in a single API call)
python onenote_to_obsidian.py --ai-tags --entities --entity-index
```

## Features

### Incremental Sync

| Scenario | What happens |
|----------|-------------|
| Page changed in OneNote only | Updated in Obsidian |
| Page changed in Obsidian only | Left alone (your edits win) |
| Both sides changed | Your file is kept, OneNote version saved as a conflict file |
| Page deleted from OneNote | Marked as orphaned, file kept |
| Page deleted from Obsidian | Not re-exported (respects your intent) |

Sync state is tracked in `.sync_state.json` inside the output folder.

### Auto-Wikilinks

On every export, the script detects mentions of other page names in body text and converts them to `[[wikilinks]]`. This runs automatically on every export (there is no flag to disable it) and respects code blocks, existing links, and frontmatter. Unresolvable `onenote://` links are preserved as plain text with a `*(unresolved OneNote link)*` annotation.

### OneNote Tags and Checkboxes

OneNote tags are automatically converted to Obsidian YAML tags (lowercase, hyphenated). OneNote checkboxes become `[ ]` / `[x]` Markdown task items. Both are handled transparently — no flags needed.

### YAML Frontmatter

Every exported page gets a YAML frontmatter block populated from OneNote metadata:

| Field | Description |
|-------|-------------|
| `tags` | OneNote tags + AI-generated tags (if `--ai-tags`) |
| `entities` | Extracted entities (if `--entities`) |
| `author` | Page creator from OneNote |
| `contributors` | Other editors from OneNote |
| `last_modified_by` | Last editor |
| `last_modified_at` | Last modification timestamp |
| `parent` | Parent page name (for sub-pages) |
| `children` | List of child page names |

These fields are queryable via Obsidian's [Dataview](https://github.com/blacksmithgu/obsidian-dataview) plugin.

### Vision AI (`--vision-ai`)

Analyzes embedded attachments using Claude's vision and text models. Each page's `_attachments/` folder is scanned, content types are auto-detected, and specialized workers generate markdown summaries.

**Supported content types:**

| Type | Detection | Output |
|------|-----------|--------|
| Slide photos | Groups of landscape images | Speaker/topic extraction, key points per slide |
| Screenshots | UI captures, app interfaces | Content description, text extraction via OCR |
| Diagrams | Org charts, pipelines, flowcharts | Structure description, entity relationships |
| Posters | Conference posters (PDF or image) | Title, authors, key findings, methods |
| Documents | PDFs, DOCX, HTML files | Summary, key sections, conclusions |
| Presentations | PPTX, landscape PDFs | Slide-by-slide content extraction |
| Tabular data | XLSX, CSV, TSV files | Schema table + AI interpretation |
| Histology slides | IHC/IF/H&E staining images | Staining type, markers, tissue morphology |

Results are written to `_ai_notes/` folders and linked from the parent page via Obsidian callout blocks:

```markdown
> [!ai]- AI Analysis — Slide Photo — Dr. Smith Keynote (12 files)
> ![[_ai_notes/slide_photo_abc123_ai.md]]
```

Analysis is cached by content hash — unchanged files are never re-analyzed.

### AI Semantic Tags (`--ai-tags`)

Calls Claude to generate 3-7 topic tags per page (skips pages with < 50 words). Tags are lowercase-hyphenated and merged into the YAML `tags:` field alongside existing OneNote tags.

```yaml
---
tags:
  - existing-onenote-tag
  - antibody-drug-conjugates
  - clinical-trials
  - oncology
---
```

Cached by content hash in sync state — pages are only re-tagged when content changes (or with `--ai-tags-force`).

### Biomedical Entity Extraction (`--entities`)

Extracts and normalizes biomedical entities from page content:

| Entity Type | Source | Example |
|---|---|---|
| Genes/Proteins | HGNC ontology (44,986 approved symbols) | EGFR, KRAS, PD-L1 |
| Diseases | MONDO ontology (31,817 diseases + 81K synonyms) | NSCLC, melanoma, AML |
| Internal compounds | Regex `M[0-9]{4}` + mapping YAML | M1774, M3814 |
| Drugs | LLM-extracted (Tier 2, when combined with `--ai-tags`) | pembrolizumab, osimertinib |

**Two-tier extraction:**
- **Tier 1 (local, ~5ms/page):** Regex + dictionary matching against HGNC and MONDO. Runs with `--entities` alone, no API calls needed.
- **Tier 2 (LLM, piggybacked):** When used with `--ai-tags`, the existing API call is extended to also return entities. Zero additional cost. Catches novel drug names not in dictionaries.

> **Note:** Drug extraction (Tier 2) only runs when `--entities` and `--ai-tags` are combined. Running `--entities` alone extracts genes, diseases, and internal compounds, but not drugs.

Entities are stored in YAML frontmatter:

```yaml
---
entities:
  genes:
    - "EGFR"
    - "KRAS"
  diseases:
    - "NSCLC"
  compounds:
    - "M1774"
---
```

With `--entity-index`, hub pages are generated in `_entity_index/` containing Dataview queries:

```
_entity_index/genes/EGFR.md      → lists all pages mentioning EGFR
_entity_index/compounds/M1774.md → lists all pages mentioning M1774
```

#### Ontology Setup

The entity dictionaries must be built before first use. Source files go in `entity_data/`:

| File | Source URL | Size |
|---|---|---|
| `hgnc_complete_set.tsv` | https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt | ~16 MB |
| `mondo.json` | https://github.com/monarch-initiative/mondo/releases/latest/download/mondo.json | ~99 MB |

Download and build:

```bash
cd entity_data/

# Download HGNC gene nomenclature (updated monthly)
curl -L -o hgnc_complete_set.tsv "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"

# Download MONDO disease ontology (updated monthly)
curl -L -o mondo.json "https://github.com/monarch-initiative/mondo/releases/latest/download/mondo.json"

# Build lookup JSONs
python build_dictionaries.py
```

This generates:
- `hgnc_genes.json` (~7 MB) — gene symbols, aliases, and HGNC IDs
- `mondo_diseases.json` (~37 MB) — disease names, synonyms, MONDO IDs, and parent relationships

Internal compound mappings are in `internal_compounds.yaml` (manually maintained). Company name variants are in `companies.yaml` (also manually maintained) and used for organization-level entity matching.

To update ontologies, re-download the source files and re-run `build_dictionaries.py`.

## Vault Structure

```
obsidian_export/
  .sync_state.json
  _entity_index/            # Generated by --entity-index
    genes/
      EGFR.md
      KRAS.md
    drugs/
      pembrolizumab.md
    diseases/
      NSCLC.md
    compounds/
      M1774.md
  Notebook Name/
    Section/
      Page.md
      _attachments/
        slide_001.png
        data.xlsx
      _ai_notes/
        slide_001_ai.md
        data_ai.md
        .vision_ai_cache.json
    Another Section/
      ...
```

## Caveats

- **OneNote must be installed** (desktop app). The script launches a COM connection.
- **First run is slow** (~30-60s for ~800 pages). Incremental syncs are fast (~10s).
- **One-way sync** — changes in Obsidian are never pushed back to OneNote.
- **Page moves** in OneNote are not tracked (appears as new page + orphan).
- **Sub-pages** are exported flat with a `parent` link in frontmatter.
- **Conflict files** are named `Page Name (OneNote conflict 2026-05-12).md`. Same-day repeats get `#2`, `#3` suffixes.
- **Vision AI costs** — each attachment group makes 1 API call. Large vaults with many images will incur costs. Use `--notebooks` to scope runs.
- **Auto-wikilinks cannot be disabled** — they run on every export. This is intentional but worth knowing if you want a plain export.
- **Tesseract OCR** — if not installed, screenshot analysis runs without text extraction (silent fallback, no error).
- **Do not run two instances on the same vault simultaneously** — `.sync_state.json` has no concurrent-write protection.

## License

MIT

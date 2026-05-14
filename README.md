# OneNote to Obsidian Sync

Exports OneNote notebooks to Obsidian-compatible Markdown and keeps them in sync on subsequent runs. Optionally enriches pages with AI analysis, semantic tags, and entity linking.

Built for corporate setups where the Obsidian OneNote plugin can't be used (no admin/OAuth access needed).

## Requirements

- **Windows** with OneNote desktop app installed and logged in
- **Python 3.10+** (recommended: [Miniconda](https://docs.conda.io/en/latest/miniconda.html))

Run the setup script to install everything:

```bash
python setup.py
```

This installs all Python packages, Tesseract OCR, and builds the entity dictionaries. See [setup.md](setup.md) for detailed step-by-step instructions.

For AI features (`--vision-ai`, `--ai-tags`), set the `ANTHROPIC_API_KEY` environment variable. See [setup.md](setup.md#step-4-configure-api-credentials) for details.

## Quick Start

```bash
# Export everything (first run = full export, later runs = incremental sync)
python onenote_to_obsidian.py

# With AI analysis and semantic tags
python onenote_to_obsidian.py --vision-ai --ai-tags

# Only sync specific notebooks
python onenote_to_obsidian.py --notebooks "Work" "Personal"

# Preview changes without writing anything
python onenote_to_obsidian.py --dry-run
```

This creates an `obsidian_export/` folder you can open directly as an Obsidian vault.

## CLI Options

| Flag | Description | Default |
|------|-------------|---------|
| `--output-dir PATH` | Where to write the vault | `./obsidian_export` |
| `--notebooks "Name1" "Name2"` | Only sync specific notebooks | All notebooks |
| `--vault-mode single\|multi` | `single` = one vault; `multi` = one vault per notebook | `single` |
| `--skip-images` | Text-only export (no images or file attachments) | Off |
| `--dry-run` | Preview what would change without writing | |
| `--force-reexport` | Re-export pages previously deleted from the vault | |
| `--force-reconvert onenote` | Overwrite all local files with fresh OneNote export | |
| `--force-reconvert obsidian` | Accept current vault files as baseline | |
| `--vision-ai` | Analyze images, PDFs, and data files with Claude | |
| `--vision-ai-force` | Re-analyze even if previously cached | |
| `--ai-tags` | Generate 3–7 semantic topic tags per page with Claude | |
| `--ai-tags-force` | Re-tag even if content hasn't changed | |
| `--no-entities` | Skip entity extraction | |
| `--entities-force` | Re-extract entities even if content hasn't changed | |
| `--dataview` | Write entities to YAML frontmatter + generate entity index pages (requires [Dataview](https://github.com/blacksmithgu/obsidian-dataview) plugin). Default: entities are native `[[wikilinks]]` | |
| `--no-entity-index` | Skip entity index generation (only with `--dataview`) | |

### Examples

```bash
# Re-export everything after a script update
python onenote_to_obsidian.py --force-reconvert onenote

# Full AI pipeline with Dataview integration
python onenote_to_obsidian.py --vision-ai --ai-tags --dataview

# Re-extract entities after updating ontology files
python onenote_to_obsidian.py --entities-force

# Fast run — skip entities entirely
python onenote_to_obsidian.py --no-entities
```

## Features

### Incremental Sync

After the first full export, only changed pages are updated:

| Scenario | What happens |
|----------|-------------|
| Page changed in OneNote only | Updated in Obsidian |
| Page changed in Obsidian only | Left alone (your edits win) |
| Both sides changed | Your file is kept, OneNote version saved as a conflict file |
| Page deleted from OneNote | Marked as orphaned, file kept |
| Page deleted from Obsidian | Not re-exported (respects your intent) |

Sync state is tracked in `.sync_state.json` inside the output folder.

### Auto-Wikilinks

The script detects mentions of other page names in body text and converts them to `[[wikilinks]]`. This builds a connected knowledge graph automatically. Generic single-word page names ("Research", "General", "Summary") are excluded to avoid noise. Code blocks, existing links, and frontmatter are left untouched.

### Code Block Detection

Text in monospace fonts (Consolas, Courier, etc.) is automatically rendered as fenced code blocks. Single-cell tables containing shell commands or code are also detected and converted.

### Tags and Checkboxes

OneNote tags become Obsidian YAML `tags:` (lowercase, hyphenated). OneNote checkboxes become `[ ]` / `[x]` task items. Both are automatic — no flags needed.

### YAML Frontmatter

Every page gets metadata from OneNote:

| Field | Description |
|-------|-------------|
| `tags` | OneNote tags + AI tags (if `--ai-tags`) |
| `entities` | Extracted entities (only with `--dataview`) |
| `author` | Page creator |
| `contributors` | Other editors |
| `last_modified_by` | Last editor |
| `last_modified_at` | Last modification timestamp |
| `parent` / `children` | Page hierarchy links |

These fields are queryable via the [Dataview](https://github.com/blacksmithgu/obsidian-dataview) plugin.

### Vision AI (`--vision-ai`)

Analyzes embedded attachments using Claude and writes linked summaries to `_ai_notes/` folders. Content types are auto-detected:

| Type | Output |
|------|--------|
| Slide photos | Speaker/topic extraction, key points |
| Screenshots | Content description + OCR text extraction |
| Diagrams | Structure description, entity relationships |
| Posters | Title, authors, key findings |
| Documents (PDF, DOCX) | Summary, key sections |
| Presentations (PPTX) | Slide-by-slide extraction |
| Tabular data (XLSX, CSV) | Schema + AI interpretation |

Results are cached by content hash — unchanged files are never re-analyzed.

AI notes include `cssclasses: [ai-generated]` and `graph_exclude: true` in their frontmatter. To hide them from the Obsidian graph: Settings > Files & Links > Excluded files > add `_ai_notes/`.

### AI Tags (`--ai-tags`)

Generates 3–7 thematic topic tags per page (e.g. `clinical-trials`, `gitlab`, `single-cell`). Tags describe *what a page is about* and are merged into the YAML `tags:` field. Pages with fewer than 50 words are skipped.

### Entity Extraction

Runs automatically once dictionaries are built (see [Setup](setup.md)). Finds specific things *mentioned* in each page:

| Entity Type | Example |
|---|---|
| Genes/Proteins | EGFR, KRAS, PD-L1 |
| Diseases | NSCLC, melanoma |
| Companies | Tempus, Caris, Merck |
| Roles | Principal Scientist, Director |
| Internal compounds | M1774, M3814 |
| Drugs (requires `--ai-tags`) | pembrolizumab, osimertinib |

Local dictionary matching finds genes, diseases, companies, roles, and compounds without any API calls. When `--ai-tags` is used, drugs, companies, and roles are additionally extracted by Claude at no extra cost (piggybacked on the same API call).

**Default mode:** entities become `[[wikilinks]]` in body text — Obsidian's backlinks panel shows all pages mentioning each entity.

**`--dataview` mode:** entities are written to YAML frontmatter, and an entity index is generated in `_entity_index/` with one hub page per entity (requires the [Dataview](https://github.com/blacksmithgu/obsidian-dataview) plugin).

#### Ontology Setup

Entity dictionaries must be built once. The setup script handles this:

```bash
python setup.py                        # full setup (packages + entities)
python setup.py --skip-packages        # only build entity dictionaries
python setup.py --update-entities      # re-download updated ontologies
```

Company names are in `entity_data/companies.yaml` (manually maintained). Internal compound mappings are in `entity_data/internal_compounds.yaml` (optional, manually maintained).

## Vault Structure

```
obsidian_export/
  .sync_state.json
  _entity_index/                # only with --dataview
    genes/EGFR.md
    companies/Tempus.md
    ...
  Notebook Name/
    Section/
      Page.md
      _attachments/
        slide_001.png
        report_a1b2c3d4.pdf
      _ai_notes/                # only with --vision-ai
        slide_001_ai.md
        report_a1b2c3d4_ai.md
        .vision_ai_cache.json
```

## Caveats

- **OneNote desktop app must be installed.** The script talks to OneNote via COM automation.
- **First run is slow** (~30–60s for ~800 pages). Incremental syncs are fast (~10s).
- **One-way sync.** Changes in Obsidian are never pushed back to OneNote.
- **Page moves** in OneNote appear as a new page + orphan.
- **Sub-pages** are exported flat with `parent`/`children` links in frontmatter.
- **Vision AI has costs.** Each attachment group makes one API call. Use `--notebooks` to scope runs.
- **Don't run two instances** on the same vault simultaneously.

## License

MIT

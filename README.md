<p align="center">
  <img src="logo.svg" width="180" alt="OneNote to Obsidian">
</p>

# OneNote to Obsidian Sync

Exports OneNote notebooks to Obsidian-compatible Markdown with incremental sync, AI-powered tagging, biomedical entity linking, and handwriting transcription.

Built for corporate setups where the Obsidian OneNote plugin can't be used (no admin/OAuth access needed).

## Requirements

- **Windows** with OneNote desktop app installed and logged in
- **Python 3.11+** (recommended: [Miniconda](https://docs.conda.io/en/latest/miniconda.html))

```bash
conda activate ds_env
python setup.py
```

See [setup.md](setup.md) for detailed instructions, including API credential setup for AI features.

## Recommended Usage

For the full pipeline with all AI features:

```bash
python onenote_to_obsidian.py --vision-ai --ai-tags --dataview
```

This gives you:
- Semantic topic tags on every page (3-7 tags like `clinical-trials`, `single-cell`)
- Biomedical entity extraction with `[[wikilinks]]` + YAML frontmatter
- Entity index pages queryable via [Dataview](https://github.com/blacksmithgu/obsidian-dataview)
- AI summaries of images, PDFs, slides, posters, and tabular data
- Handwritten ink transcription

The first run does a full export. Subsequent runs are incremental — only changed pages are re-processed.

### Other common patterns

```bash
# Basic export without AI (no API credentials needed)
python onenote_to_obsidian.py

# Add AI tags to an existing export (reads from disk, no OneNote re-export)
python onenote_to_obsidian.py --ai-tags

# Only sync specific notebooks
python onenote_to_obsidian.py --notebooks "Work" "Personal"

# Exclude specific notebooks from sync
python onenote_to_obsidian.py --exclude-notebooks "Archive" "Personal"

# Preview changes without writing anything
python onenote_to_obsidian.py --dry-run

# Re-export everything after a script update
python onenote_to_obsidian.py --force-reconvert onenote

# Add vision-ai retroactively (reads images from disk, no re-export needed)
python onenote_to_obsidian.py --vision-ai --ai-tags
```

## CLI Reference

### Sync control

| Flag | Description |
|------|-------------|
| `--output-dir PATH` | Where to write the vault (default: `./obsidian_export`) |
| `--notebooks "Name1" "Name2"` | Only sync specific notebooks (default: all) |
| `--exclude-notebooks "Name1" "Name2"` | Exclude specific notebooks from sync |
| `--vault-mode single\|multi` | `single` = one vault; `multi` = one vault per notebook |
| `--skip-images` | Text-only export (no images or file attachments) |
| `--dry-run` | Preview what would change without writing |
| `--force-reexport` | Re-export pages previously deleted from the vault |
| `--force-reconvert onenote` | Overwrite all local files with fresh OneNote export |
| `--force-reconvert obsidian` | Accept current vault files as baseline |

### AI features (require API credentials — see [setup.md](setup.md#step-3-configure-api-credentials))

| Flag | Description |
|------|-------------|
| `--ai-tags` | Generate semantic topic tags per page (Haiku). Can be added to an existing export — untagged pages are processed from disk without re-exporting from OneNote. |
| `--ai-tags-force` | Re-tag all pages, even if already tagged |
| `--vision-ai` | Analyze images, PDFs, data files, and transcribe handwriting (Sonnet). Can be added to an existing export — images on disk are analyzed without re-exporting from OneNote. Only ink transcription requires `--force-reconvert onenote` (needs rendered PDFs). |
| `--vision-ai-force` | Re-analyze even if previously cached |

### Entity extraction (runs automatically, no API needed)

| Flag | Description |
|------|-------------|
| `--dataview` | Write entities to YAML frontmatter + generate entity index pages. Without this, entities are native `[[wikilinks]]` in body text. |
| `--no-entities` | Skip entity extraction entirely |
| `--entities-force` | Re-extract entities even if content hasn't changed |
| `--no-entity-index` | Skip entity index generation (only with `--dataview`) |

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

### AI Tags (`--ai-tags`)

Generates 3-7 thematic topic tags per page (e.g. `clinical-trials`, `gitlab`, `single-cell`). Tags describe *what a page is about* and are merged into the YAML `tags:` field. Pages with fewer than 50 words are skipped.

`--ai-tags` can be added at any time. Pages that were previously synced without it are automatically tagged from their on-disk markdown — no OneNote re-export needed. Use `--ai-tags-force` to re-tag all pages.

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
| **Handwritten ink** | **Full text transcription inline in page** |

Results are cached by content hash — unchanged files are never re-analyzed.

Like `--ai-tags`, vision AI can be added to an existing export — it reads images from the on-disk `_attachments/` folders. Only handwritten ink transcription requires `--force-reconvert onenote` (it needs a rendered PDF from OneNote COM).

AI notes include `cssclasses: [ai-generated]` and `graph_exclude: true` in their frontmatter. To hide them from the Obsidian graph: Settings > Files & Links > Excluded files > add `_ai_notes/`.

#### Handwriting Recognition

Pages with handwritten ink (stylus/pen input) are automatically detected. When `--vision-ai` is active, the page is rendered as PDF and sent to Claude Vision for transcription. The result is inserted under a `## Handwritten Notes` section.

Without `--vision-ai`, ink pages still get a placeholder. If OneNote has built-in recognition data, that text is used as a free fallback.

### Entity Extraction

Runs automatically once dictionaries are built (see [setup.md](setup.md)). Finds specific things *mentioned* in each page:

| Entity Type | Example |
|---|---|
| Genes/Proteins | EGFR, KRAS, PD-L1 |
| Diseases | NSCLC, melanoma, glioblastoma |
| Companies | Tempus, Caris, Merck |
| Drugs (with `--ai-tags`) | pembrolizumab, osimertinib |
| Methods | scRNA-seq, CRISPR, ADC, PROTAC |
| Clinical trials | KEYNOTE-158, NCT03482401 |
| Cell lines | A549, HCT116, MCF-7 |
| Conferences | AACR, ASCO, ESMO |
| Pathways | PI3K/AKT, Wnt, MAPK |
| Roles | Principal Scientist, Head of Computational Biology |
| Departments | RU ONC, DDTech, TBR |
| Internal compounds | M1774, M3814 |

Local dictionary matching runs without any API calls. When `--ai-tags` is used, drugs and methods are additionally extracted by Claude at no extra cost (piggybacked on the same API call).

**Default mode:** entities become `[[wikilinks]]` in body text — Obsidian's backlinks panel shows all pages mentioning each entity.

**`--dataview` mode:** entities are also written to YAML frontmatter, and an entity index is generated in `_entity_index/` with one hub page per entity.

#### Ontology Setup

Entity dictionaries are built by the setup script:

```bash
python setup.py                        # full setup (packages + entities)
python setup.py --skip-packages        # only build entity dictionaries
python setup.py --update-entities      # re-download updated ontologies
```

Curated dictionaries in `entity_data/` (manually maintained):

| File | Content |
|------|---------|
| `companies.yaml` | Company names with parent/subsidiary hierarchy |
| `methods.yaml` | Experimental methods, technologies, drug modalities |
| `departments.yaml` | Internal organizational units |
| `clinical_trials.yaml` | Named clinical trials (KEYNOTE, CheckMate, etc.) |
| `cell_lines.yaml` | Cancer cell line names |
| `conferences.yaml` | Scientific conferences and congresses |
| `pathways.yaml` | Signaling pathways and biological processes |
| `internal_compounds.yaml` | Optional metadata for M-number compounds |

### Auto-Wikilinks

Mentions of other page names in body text are automatically converted to `[[wikilinks]]`. Generic single-word page names ("Research", "General") are excluded to avoid noise. Code blocks, existing links, and frontmatter are left untouched.

### Code Block Detection

Monospace-styled text (Consolas, Courier, etc.) becomes fenced code blocks. A rescue pass also detects R/Python code that wasn't styled with monospace and wraps it.

### Tags and Checkboxes

OneNote tags become Obsidian YAML `tags:` (lowercase, hyphenated). Checkboxes become `[ ]` / `[x]` task items. Both are automatic.

### YAML Frontmatter

Every page gets metadata from OneNote:

| Field | Description |
|-------|-------------|
| `tags` | OneNote tags + AI tags (if `--ai-tags`) |
| `entities` | Extracted entities (only with `--dataview`) |
| `author` | Page creator |
| `contributors` | Other editors |
| `last_modified_by` / `last_modified_at` | Last editor and timestamp |
| `parent` / `children` | Page hierarchy links |

## Vault Structure

```
obsidian_export/
  .sync_state.json
  _entity_index/                # only with --dataview
    genes/EGFR.md
    companies/Tempus.md
  Notebook Name/
    Section/
      Page.md
      _attachments/
        slide_001.png
      _ai_notes/                # only with --vision-ai
        slide_001_ai.md
        .vision_ai_cache.json
```

## Post-Processing Tools

### `tools/fix_export.py`

Applies fixes to an existing export without re-running the full pipeline:

```bash
python tools/fix_export.py --dry-run   # preview
python tools/fix_export.py             # apply
```

Fixes: HTML entities, false disease wikilinks, OneNote citation format, unescaped code blocks, gene alias normalization, duplicate entity index files.

## API Cost Breakdown

| Task | Model | Trigger | Input/call | Output/call |
|------|-------|---------|------------|-------------|
| Image analysis | Sonnet 4.6 | `--vision-ai` | ~2K-25K tokens | ~500-2K tokens |
| Ink transcription | Sonnet 4.6 | `--vision-ai` + ink | ~2K-8K tokens | ~500-2K tokens |
| Tagging + entity extraction | Haiku 4.5 | `--ai-tags` | ~1.1K tokens | ~200 tokens |
| Tabular data | Sonnet 4.6 | `--vision-ai` + XLSX/CSV | ~800 tokens | ~300 tokens |

### Estimated costs (~800 pages, ~500 image groups)

| Feature | API calls | Est. cost |
|---------|-----------|-----------|
| `--ai-tags` | ~800 | ~$1.50 |
| `--vision-ai` | ~500 | ~$6-10 |
| **Full pipeline** | ~1,300 | **~$8-12** |

Incremental runs only process changed pages — typically 5-20 API calls (~$0.05-$0.30).

**Why Haiku for tagging?** Tagging and entity extraction are structured text-to-JSON tasks with short inputs. Haiku handles these at equivalent quality for ~3x less cost. Vision tasks require Sonnet's multimodal reasoning.

## Caveats

- **OneNote desktop app must be installed** — the script uses COM automation.
- **First run is slow** (~30-60s for ~800 pages). Incremental syncs are fast (~10s).
- **One-way sync.** Changes in Obsidian are never pushed back to OneNote.
- **Ink transcription** requires `--force-reconvert onenote --vision-ai` (needs rendered PDF from COM). Image/document analysis can be added retroactively.
- **Page moves** in OneNote appear as a new page + orphan.
- **Sub-pages** are exported flat with `parent`/`children` links in frontmatter.
- **Don't run two instances** on the same vault simultaneously.

## License

MIT

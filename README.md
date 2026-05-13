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
| `--force-reconvert onenote` | Overwrite all local files with fresh OneNote export (destructive) |
| `--force-reconvert obsidian` | Accept current vault as baseline without touching files |

### AI Features

| Flag | Description |
|------|-------------|
| `--vision-ai` | Analyze embedded images, PDFs, and data files with Claude Vision. Creates `_ai_notes/` with linked summaries |
| `--vision-ai-force` | Re-analyze attachments even if previously cached |
| `--ai-tags` | Generate 3-7 semantic topic tags per page using Claude. Tags are added to YAML frontmatter |
| `--ai-tags-force` | Re-tag pages even if content hasn't changed |

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

On every export, the script detects mentions of other page names in body text and converts them to `[[wikilinks]]`. This runs automatically (no flag needed) and respects code blocks, existing links, and frontmatter.

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

## Vault Structure

```
obsidian_export/
  .sync_state.json
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
- **Conflict files** are named `Page Name (OneNote conflict 2026-05-12).md`.
- **Vision AI costs** — each attachment group makes 1 API call. Large vaults with many images will incur costs. Use `--notebooks` to scope runs.

## License

MIT

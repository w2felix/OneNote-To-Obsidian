# OneNote to Obsidian Sync

A Python script that exports your OneNote notebooks to Obsidian-compatible Markdown files — and keeps them in sync on subsequent runs.

Built for environments where the Obsidian OneNote plugin can't be used (e.g. corporate setups without admin/OAuth access).

## Requirements

- **Windows** with OneNote desktop app installed and logged in
- **Python 3.10+** with:
  ```
  pip install beautifulsoup4
  ```

> The script uses PowerShell COM automation to talk to OneNote. No browser login or admin rights needed — if OneNote opens and shows your notebooks, you're good.

## Quick Start

```bash
# Export everything (first run = full export, subsequent runs = incremental sync)
python onenote_to_obsidian.py
```

This creates an `obsidian_export/` folder you can open directly as an Obsidian vault.

## Options

| Flag | What it does | Default |
|------|-------------|---------|
| `--output-dir PATH` | Where to write the vault | `./obsidian_export` |
| `--notebooks "Name1" "Name2"` | Only sync specific notebooks | All notebooks |
| `--vault-mode single\|multi` | One vault for everything, or one folder per notebook | `single` |
| `--skip-images` | Text only (faster, much smaller output) | Images included |
| `--dry-run` | Preview what would change without writing anything | — |
| `--force-reexport` | Re-export pages you previously deleted from the vault | — |
| `--force-reconvert onenote` | Re-export all pages from OneNote, overwriting local files | — |
| `--force-reconvert obsidian` | Keep all local files as-is, reset sync state to match them | — |

### Examples

```bash
# Only sync two notebooks
python onenote_to_obsidian.py --notebooks "Work" "Personal"

# Each notebook becomes its own vault folder
python onenote_to_obsidian.py --vault-mode multi

# See what changed since last sync without touching any files
python onenote_to_obsidian.py --dry-run

# Re-export everything from OneNote (e.g. after a script update that improves conversion)
python onenote_to_obsidian.py --force-reconvert onenote

# Keep your Obsidian edits and just reset the sync baseline
python onenote_to_obsidian.py --force-reconvert obsidian
```

## How Sync Works

| Scenario | What happens |
|----------|-------------|
| Page changed in OneNote only | Updated in Obsidian |
| Page changed in Obsidian only | Left alone (your edits win) |
| Both sides changed | Your Obsidian file is kept, OneNote version saved as a separate conflict file |
| Page deleted from OneNote | Marked as orphaned, your file is kept |
| Page deleted from Obsidian | Not re-exported (respects your intent) |

Sync state is tracked in `.sync_state.json` inside the output folder. Don't delete this file — it's how the script knows what changed.

## Caveats

- **OneNote must be running** (or at least installed). The script launches a COM connection to it.
- **First run takes a while** (~30–60 seconds for ~800 pages). Incremental syncs are much faster (~10 seconds).
- **Images and file attachments** (PDFs, Word, Excel, etc.) are stored in `_attachments/` folders next to the pages. PDFs render inline; other files are linked.
- **Sub-pages** are exported flat (not nested folders) with a `parent` link in the frontmatter.
- **Page moves** in OneNote are not tracked — if you move a page to a different section, it will appear as a new page and the old file becomes orphaned.
- **One-way sync**: changes in Obsidian are never pushed back to OneNote.
- **Conflict files** look like `Page Name (OneNote conflict 2026-05-12).md` — resolve them manually and delete the conflict copy when done.

## Vault Structure

```
obsidian_export/
  .sync_state.json
  Notebook Name/
    Section/
      Page.md
      _attachments/
        a3b8f9c2e1d0.png
    Another Section/
      ...
  Another Notebook/
    ...
```

## License

MIT

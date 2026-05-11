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

### Basic

| Flag | What it does | Default |
|------|-------------|---------|
| `--output-dir PATH` | Where to write the vault | `./obsidian_export` |
| `--notebooks "Name1" "Name2"` | Only sync specific notebooks (use exact names as they appear in OneNote) | All notebooks |
| `--vault-mode single\|multi` | `single` = one vault containing all notebooks as top-level folders. `multi` = each notebook gets its own separate vault folder (useful if you want to open them independently in Obsidian) | `single` |
| `--skip-images` | Skip all images and file attachments. Produces text-only Markdown — much faster and smaller | Images included |

### Sync control

| Flag | When to use it |
|------|---------------|
| `--dry-run` | You want to preview what the script *would* do without actually writing or changing anything. Good for checking before a big sync. |
| `--force-reexport` | You previously deleted a page from the Obsidian vault (intentionally), but now want it back. Normally the script respects deletions — this flag overrides that. |
| `--force-reconvert onenote` | You want to **overwrite all local Markdown files** with a fresh export from OneNote. Use this after a script update that improves formatting, or if your local files got corrupted. All your Obsidian edits will be lost. |
| `--force-reconvert obsidian` | You've been editing files in Obsidian (fixing formatting, reorganizing content, etc.) and want the sync engine to **accept your current vault as the new baseline**. No files are touched — it only resets the internal tracking so that future syncs won't flag your edits as conflicts. |

### Examples

```bash
# Only sync two notebooks
python onenote_to_obsidian.py --notebooks "Work" "Personal"

# Each notebook becomes its own vault folder
python onenote_to_obsidian.py --vault-mode multi

# See what changed since last sync without touching any files
python onenote_to_obsidian.py --dry-run

# After a script update: re-export everything with improved conversion
python onenote_to_obsidian.py --force-reconvert onenote

# After bulk-editing in Obsidian: tell the sync "my local files are correct now"
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

# Setup Guide

Complete installation instructions for OneNote to Obsidian Sync.

---

## Step 1: Install Miniconda

### Download

1. Go to https://docs.conda.io/en/latest/miniconda.html
2. Download **Miniconda3 Windows 64-bit**
3. Run the installer

### Installation

- Install for: Just Me (recommended)
- Location: Default (`C:\Users\YourName\miniconda3`)
- Add to PATH: ☑ (optional but recommended)

### Verify

```bash
conda --version
# Should output: conda 23.x.x or newer
```

---

## Step 2: Create Conda Environment

```bash
# Create environment with Python 3.11
conda create -n ds_env python=3.11 -y

# Activate
conda activate ds_env

# Verify
python --version
# Should output: Python 3.11.x
```

---

## Step 3: Install Everything

Run the setup script — it installs all pip packages, conda packages (pandas, openpyxl, Tesseract), downloads the entity ontology files, and builds the lookup dictionaries in one go:

```bash
conda activate ds_env
python setup.py
```

This takes a few minutes (downloads ~115 MB of ontology data). When it finishes it prints a verification summary.

**Options:**

```bash
# Re-download and rebuild entity ontologies (updated monthly)
python setup.py --update-entities

# Skip package installation (only refresh entity dictionaries)
python setup.py --skip-packages --update-entities
```

> If you are not inside a conda environment, Tesseract cannot be installed automatically. The script will install the remaining packages via pip and print manual Tesseract install instructions.

---

## Step 4: Configure API Credentials

Required for `--vision-ai` and `--ai-tags`. Set credentials as Windows User environment variables:

```powershell
# Corporate proxy setup (ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL)
[Environment]::SetEnvironmentVariable("ANTHROPIC_AUTH_TOKEN", "your-token", "User")
[Environment]::SetEnvironmentVariable("ANTHROPIC_BASE_URL", "https://your-proxy-url/api/proxy/anthropic", "User")

# Verify
[Environment]::GetEnvironmentVariable('ANTHROPIC_AUTH_TOKEN', 'User')
[Environment]::GetEnvironmentVariable('ANTHROPIC_BASE_URL', 'User')
```

If using the Anthropic API directly (no proxy), set `ANTHROPIC_API_KEY` instead:

```powershell
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")
```

**IMPORTANT**: Restart your terminal after setting variables.

---

## Step 5: Verify OneNote is Ready

The script uses PowerShell COM automation to talk to OneNote — no browser login or admin rights needed.

- Open **OneNote desktop app** and sign in
- Confirm your notebooks are visible and synced
- That's it — if OneNote shows your notebooks, the script can read them

---

## Step 6: First Run

```bash
conda activate ds_env
cd OneNote_To_Obsidian

# Basic sync (exports all notebooks to obsidian_export/)
python onenote_to_obsidian.py

# Full AI pipeline (requires API credentials)
python onenote_to_obsidian.py --vision-ai --ai-tags
```

See the [README](README.md) for all CLI options.

---

## Troubleshooting

### Tesseract Not Found

```powershell
# Add Tesseract to PATH
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\ProgramData\miniconda3\envs\ds_env\Library\bin", "User")

# Restart terminal, then verify
tesseract --version
```

### API Credentials Not Working

```powershell
# Check current values
[Environment]::GetEnvironmentVariable('ANTHROPIC_AUTH_TOKEN', 'User')
[Environment]::GetEnvironmentVariable('ANTHROPIC_BASE_URL', 'User')

# Re-set if empty
[Environment]::SetEnvironmentVariable("ANTHROPIC_AUTH_TOKEN", "your-token", "User")
[Environment]::SetEnvironmentVariable("ANTHROPIC_BASE_URL", "https://your-proxy-url/api/proxy/anthropic", "User")

# MUST restart terminal after changes
```

### OneNote COM Error

- Make sure OneNote desktop app is installed and open
- Sign in and wait for notebooks to fully sync
- Run the script from a regular terminal (not elevated/admin)

### Package Import Errors

```bash
# Verify environment is active
conda activate ds_env
where python
# Should show: C:\Users\YourName\miniconda3\envs\ds_env\python.exe

# Reinstall a package
conda install -c conda-forge <package-name> -y
# or
pip install <package-name>
```

### Entity Dictionaries Missing

```bash
# Re-run setup (skips package install, only refreshes dictionaries)
python setup.py --skip-packages
```

---

## Installation Checklist

- [ ] Install Miniconda
- [ ] Create `ds_env` environment (Python 3.11)
- [ ] Run `python setup.py` (installs packages, Tesseract, and entity dictionaries)
- [ ] Set `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL` (or `ANTHROPIC_API_KEY`)
- [ ] Restart terminal
- [ ] Open OneNote desktop app and confirm notebooks are synced
- [ ] Run `python onenote_to_obsidian.py` to verify everything works

---

## Package Reference

| Package | Purpose | Install |
|---------|---------|---------|
| defusedxml | Safe XML parsing (OneNote export) | `pip install defusedxml` |
| beautifulsoup4 | HTML/XML content parsing | `pip install beautifulsoup4` |
| anthropic | Claude API client (Vision AI + tags) | `pip install anthropic` |
| pymupdf (fitz) | PDF rendering to images | `conda install -c conda-forge pymupdf` |
| pillow | Image processing | `conda install -c conda-forge pillow` |
| pdfplumber | PDF text extraction | `conda install -c conda-forge pdfplumber` |
| python-pptx | PPTX text/slide extraction | `pip install python-pptx` |
| python-docx | DOCX text extraction | `pip install python-docx` |
| pandas | Tabular data reading | `conda install pandas` |
| openpyxl | Excel file support | `conda install openpyxl` |
| tabulate | Markdown table formatting | `pip install tabulate` |
| pytesseract | OCR interface (Tesseract) | `conda install -c conda-forge pytesseract` |
| PyYAML | YAML file parsing (entity data) | `pip install PyYAML` |

# Setup Guide

Step-by-step installation for OneNote to Obsidian Sync.

---

## Step 1: Install Miniconda

> Miniconda can be installed without admin rights — select "Just Me" during installation.

1. Download [Miniconda3 Windows 64-bit](https://docs.conda.io/en/latest/miniconda.html)
2. Run the installer:
   - Install for: **Just Me**
   - Location: default (`C:\Users\YourName\miniconda3`)
   - Add to PATH: check the box (optional but convenient)

Verify:

```bash
conda --version
# Should output: conda 23.x.x or newer
conda init powershell   # or: conda init bash
# then restart the terminal
```

---

## Step 2: Create Environment

```bash
conda create -n ds_env python=3.11 -y
conda activate ds_env
python --version
# Should output: Python 3.11.x
```

---

## Step 3: Run Setup Script

The setup script installs all packages, Tesseract OCR, and builds entity dictionaries:

```bash
conda activate ds_env
python setup.py
```

This takes a few minutes (downloads ~115 MB of ontology data). It prints a verification summary when done.

**Other options:**

```bash
# Re-download ontologies (updated monthly)
python setup.py --update-entities

# Only refresh entity dictionaries (skip package install)
python setup.py --skip-packages --update-entities

# Set up Tesseract separately (see below)
python setup.py --setup-tesseract
```

---

## Step 4: Configure API Credentials

Required only for `--vision-ai` and `--ai-tags`.

**Corporate proxy setup:**

```powershell
[Environment]::SetEnvironmentVariable("ANTHROPIC_AUTH_TOKEN", "your-token", "User")
[Environment]::SetEnvironmentVariable("ANTHROPIC_BASE_URL", "https://your-proxy-url/api/proxy/anthropic", "User")
```

**Direct Anthropic API:**

```powershell
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")
```

Restart your terminal after setting variables.

---

## Step 5: OneNote

The script uses COM automation — no browser login or admin rights needed.

1. Open **OneNote desktop app** and sign in
2. Confirm your notebooks are visible and synced
3. Done — if OneNote shows your notebooks, the script can read them

---

## Step 6: First Run

```bash
conda activate ds_env
python onenote_to_obsidian.py

# With AI features (requires API credentials)
python onenote_to_obsidian.py --vision-ai --ai-tags
```

See the [README](README.md) for all CLI options.

---

## Tesseract OCR (without admin rights)

Tesseract is optional — it enables text extraction from screenshots. Without it, screenshot analysis still runs but can't read text in images.

### Option A: Automatic (conda)

If you're using conda, setup.py installs Tesseract automatically:

```bash
conda activate ds_env
python setup.py
```

### Option B: Manual download (no admin rights)

Use this if you don't have conda or if the conda install didn't work.

**Step 1 — Download:**

```bash
python setup.py --setup-tesseract
```

This downloads the installer to your temp folder and walks you through the setup.

**Step 2 — Install for current user:**

When the installer opens:
- Select **"Install for current user only"**
- Set install location to: `%LOCALAPPDATA%\Programs\Tesseract-OCR`
  (typically `C:\Users\YourName\AppData\Local\Programs\Tesseract-OCR`)
- Complete the installation

**Step 3 — Verify:**

The setup script automatically adds Tesseract to your User PATH. Restart your terminal, then:

```bash
tesseract --version
```

### Option C: Manual extraction with 7-Zip (fully portable, no installer)

If you can't run the installer at all:

1. Download the installer `.exe` from [UB-Mannheim releases](https://github.com/UB-Mannheim/tesseract/wiki)
2. Right-click the `.exe` → **7-Zip** → **Extract to folder**
3. Copy the extracted folder to:
   ```
   %LOCALAPPDATA%\Programs\Tesseract-OCR
   ```
4. Tell setup.py where it is:
   ```bash
   python setup.py --tesseract-path "%LOCALAPPDATA%\Programs\Tesseract-OCR"
   ```
   This configures PATH and TESSDATA_PREFIX automatically.

### Option D: Point to existing installation

If Tesseract is already installed somewhere:

```bash
python setup.py --tesseract-path "C:\path\to\Tesseract-OCR"
```

---

## Troubleshooting

### Tesseract not found

```bash
# Re-run Tesseract setup
python setup.py --setup-tesseract

# Or point to an existing installation
python setup.py --tesseract-path "C:\path\to\Tesseract-OCR"
```

If the PATH was set but the terminal doesn't find it: **restart your terminal** (PATH changes require a new shell session).

### API credentials not working

```powershell
# Check current values
[Environment]::GetEnvironmentVariable('ANTHROPIC_AUTH_TOKEN', 'User')
[Environment]::GetEnvironmentVariable('ANTHROPIC_BASE_URL', 'User')
```

Restart your terminal after any changes.

### OneNote COM error

- Make sure OneNote desktop app is installed and open
- Sign in and wait for notebooks to fully sync
- Run from a regular terminal (not elevated/admin)

### Package import errors

```bash
conda activate ds_env
where python
# Should show: C:\Users\YourName\miniconda3\envs\ds_env\python.exe

# Reinstall a specific package
pip install --upgrade <package-name>
```

### Entity dictionaries missing

```bash
python setup.py --skip-packages
```

---

## Checklist

- [ ] Install Miniconda (no admin — "Just Me")
- [ ] Create `ds_env` environment
- [ ] Run `python setup.py`
- [ ] Set API credentials (if using AI features)
- [ ] Restart terminal
- [ ] Open OneNote and confirm notebooks are synced
- [ ] Run `python onenote_to_obsidian.py`

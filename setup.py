"""One-stop setup: install dependencies, Tesseract OCR, and entity dictionaries.

Run once after cloning the repo:
    python setup.py

Re-run to update entity ontologies (updated monthly):
    python setup.py --update-entities

Set up Tesseract manually (no admin rights):
    python setup.py --setup-tesseract
"""

import argparse
import hashlib
import os
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

# Use Windows system certificate store so corporate proxy CAs are trusted
_ssl_context = ssl.create_default_context()
_ssl_context.load_default_certs()
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_context))
)

DATA_DIR = Path(__file__).parent / 'entity_data'
REQUIREMENTS_TXT = Path(__file__).parent / 'requirements.txt'

# ── Packages ──────────────────────────────────────────────────────────────────

# Installed via conda when available (better binary compatibility on Windows)
CONDA_PACKAGES = [
    'pandas',
    'openpyxl',
    'pytesseract',
]

# ── Tesseract ────────────────────────────────────────────────────────────────

TESSERACT_DOWNLOAD_URL = (
    'https://github.com/UB-Mannheim/tesseract/releases/download/'
    'v5.5.0.20241111/tesseract-ocr-w64-setup-5.5.0.20241111.exe'
)
TESSERACT_FILENAME = 'tesseract-ocr-w64-setup-5.5.0.20241111.exe'
# Set to None to skip verification (upstream doesn't publish checksums)
TESSERACT_SHA256 = None

def _tesseract_search_paths() -> list[Path]:
    """Build search paths, skipping any based on unset env vars."""
    paths = []
    localappdata = os.environ.get('LOCALAPPDATA')
    if localappdata:
        paths.append(Path(localappdata) / 'Programs' / 'Tesseract-OCR')
        paths.append(Path(localappdata) / 'Programs' / 'tesseract')
    conda_prefix = os.environ.get('CONDA_PREFIX')
    if conda_prefix:
        paths.append(Path(conda_prefix) / 'Library' / 'bin')
    paths += [
        Path(r'C:\Program Files\Tesseract-OCR'),
        Path(r'C:\Program Files (x86)\Tesseract-OCR'),
        Path.home() / 'AppData' / 'Local' / 'Programs' / 'Tesseract-OCR',
        Path.home() / 'AppData' / 'Local' / 'Programs' / 'tesseract',
    ]
    return paths


TESSERACT_SEARCH_PATHS = _tesseract_search_paths()

# eng.traineddata download (fast variant, ~4 MB)
ENG_TRAINEDDATA_URL = (
    'https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata'
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def section(title: str):
    print(f'\n{"=" * 60}')
    print(f'  {title}')
    print('=' * 60)


def run(cmd: list[str], description: str) -> bool:
    print(f'\n  > {" ".join(cmd)}')
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f'  ERROR: {description} failed (exit {result.returncode})', file=sys.stderr)
        return False
    return True


def verify_checksum(path: Path, expected_sha256: str) -> bool:
    """Verify SHA-256 checksum of a downloaded file."""
    sha256 = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    actual = sha256.hexdigest()
    if actual != expected_sha256:
        print(f'  [!] Checksum mismatch for {path.name}', file=sys.stderr)
        print(f'      Expected: {expected_sha256}', file=sys.stderr)
        print(f'      Got:      {actual}', file=sys.stderr)
        return False
    return True


def conda_executable() -> str | None:
    """Return path to conda if we are inside a conda environment."""
    conda_prefix = os.environ.get('CONDA_PREFIX')
    if not conda_prefix:
        return None
    for candidate in ['conda', os.path.join(conda_prefix, '..', '..', 'Scripts', 'conda')]:
        try:
            result = subprocess.run(
                [candidate, '--version'], capture_output=True, text=True)
            if result.returncode == 0:
                return candidate
        except FileNotFoundError:
            continue
    return None


# ── Package Installation ─────────────────────────────────────────────────────

def ensure_pip():
    """Ensure pip is available in the current environment."""
    result = subprocess.run(
        [sys.executable, '-m', 'pip', '--version'], capture_output=True, text=True)
    if result.returncode == 0:
        return True
    print('  pip not found — attempting to bootstrap...')
    result = subprocess.run(
        [sys.executable, '-m', 'ensurepip', '--default-pip'], capture_output=True, text=True)
    if result.returncode == 0:
        print('  pip bootstrapped successfully.')
        return True
    print('  [!] Could not install pip. Install it manually:', file=sys.stderr)
    print('      python -m ensurepip --default-pip', file=sys.stderr)
    return False


def install_pip_packages():
    section('Installing pip packages')
    if not REQUIREMENTS_TXT.exists():
        print(f'  ERROR: {REQUIREMENTS_TXT} not found', file=sys.stderr)
        return
    if not ensure_pip():
        return
    ok = run(
        [sys.executable, '-m', 'pip', 'install', '--upgrade', '-r', str(REQUIREMENTS_TXT)],
        'pip install -r requirements.txt',
    )
    if not ok:
        print('  Some pip packages failed — check output above.', file=sys.stderr)


def install_conda_packages(conda: str):
    section('Installing conda packages (pandas, openpyxl, pytesseract)')
    run(
        [conda, 'install', '-c', 'conda-forge', '--yes'] + CONDA_PACKAGES,
        'conda install',
    )


def warn_no_conda():
    print('\n  [i] Not running inside a conda environment.')
    print('      All packages will be installed via pip (from requirements.txt).')


# ── Tesseract Detection & Installation ──────────────────────────────────────

def find_tesseract() -> Path | None:
    """Search common locations for the Tesseract binary."""
    # Check PATH first
    try:
        result = subprocess.run(
            ['tesseract', '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            where = subprocess.run(
                ['where', 'tesseract'], capture_output=True, text=True)
            if where.returncode == 0:
                return Path(where.stdout.strip().splitlines()[0]).parent
            return Path('tesseract')  # In PATH but can't resolve directory
    except FileNotFoundError:
        pass

    # Search known locations
    for search_path in TESSERACT_SEARCH_PATHS:
        candidate = search_path / 'tesseract.exe'
        if candidate.exists():
            return search_path

    return None


def set_user_env_var(name: str, value: str):
    """Set a Windows User environment variable via winreg."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Environment',
            0, winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f'  [!] Could not set {name}: {e}')
        return False


def add_to_user_path(directory: str):
    """Add a directory to the User PATH environment variable."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Environment',
            0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        )
        try:
            current_path, _ = winreg.QueryValueEx(key, 'Path')
        except FileNotFoundError:
            current_path = ''

        # Check if already in PATH
        paths = [p.strip() for p in current_path.split(';') if p.strip()]
        dir_lower = directory.lower()
        if any(p.lower() == dir_lower for p in paths):
            print(f'  {directory} already in User PATH.')
            winreg.CloseKey(key)
            return True

        new_path = current_path.rstrip(';') + ';' + directory
        winreg.SetValueEx(key, 'Path', 0, winreg.REG_EXPAND_SZ, new_path)
        winreg.CloseKey(key)
        print(f'  Added {directory} to User PATH.')
        return True
    except Exception as e:
        print(f'  [!] Could not modify PATH: {e}')
        return False


def setup_tesseract_manual():
    """Guide user through no-admin Tesseract installation."""
    section('Tesseract OCR Setup (no admin rights)')

    # Check if already installed
    existing = find_tesseract()
    if existing:
        print(f'\n  Tesseract found at: {existing}')
        configure_tesseract_path(existing)
        return

    install_dir = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Tesseract-OCR'

    print(f'''
  Tesseract OCR is needed for text extraction from screenshots.
  It will be installed to: {install_dir}

  Steps:
  1. Download the installer from GitHub
  2. Extract it using the installer (select "Install for current user")
     Install location: {install_dir}
  3. This script will configure the PATH automatically

  Alternatively, if you have 7-Zip:
  1. Download the installer .exe
  2. Right-click > 7-Zip > Extract to folder
  3. Copy the extracted folder to: {install_dir}
''')

    # Try to download
    download_dir = Path(os.environ.get('TEMP', '.'))
    dest = download_dir / TESSERACT_FILENAME

    if dest.exists():
        print(f'  Installer already downloaded: {dest}')
    else:
        print(f'  Downloading Tesseract installer ({TESSERACT_FILENAME})...')
        try:
            def progress(block_count, block_size, total_size):
                if total_size > 0:
                    pct = min(100, block_count * block_size * 100 // total_size)
                    print(f'\r    {pct}%', end='', flush=True)
            urllib.request.urlretrieve(TESSERACT_DOWNLOAD_URL, dest, reporthook=progress)
            print(f'\r  Downloaded to: {dest}')
        except Exception as e:
            print(f'\n  Download failed: {e}')
            print(f'  Manual download: {TESSERACT_DOWNLOAD_URL}')
            return

    # Verify integrity before executing
    if TESSERACT_SHA256:
        if not verify_checksum(dest, TESSERACT_SHA256):
            print('  [!] Downloaded file failed integrity check — aborting.', file=sys.stderr)
            print('  The file may be corrupted or tampered with. Delete it and retry,')
            print(f'  or download manually: {TESSERACT_DOWNLOAD_URL}')
            return

    # Offer to run the installer for current user
    print(f'\n  Running installer (select "Install for current user only")...')
    print(f'  Install to: {install_dir}')
    try:
        subprocess.run([str(dest), f'/D={install_dir}'], check=False)
    except Exception as e:
        print(f'  Could not run installer: {e}')
        print(f'  Run it manually: {dest}')
        print(f'  Set install location to: {install_dir}')

    # Check if it worked
    if (install_dir / 'tesseract.exe').exists():
        print(f'\n  Tesseract installed successfully!')
        configure_tesseract_path(install_dir)
    else:
        print(f'\n  Tesseract not found at expected location.')
        print(f'  If you installed to a different folder, run:')
        print(f'    python setup.py --setup-tesseract --tesseract-path "C:\\path\\to\\Tesseract-OCR"')


def find_tessdata(tesseract_dir: Path) -> Path | None:
    """Search for the tessdata directory relative to the Tesseract binary.

    Sync with: vision_ai/ocr_utils.py:_find_tessdata (kept separate because
    setup.py must remain standalone — it runs before the package is importable).
    """
    candidates = [
        tesseract_dir / 'tessdata',
        tesseract_dir.parent / 'tessdata',
        tesseract_dir.parent / 'share' / 'tessdata',
        tesseract_dir / 'share' / 'tessdata',
    ]
    for candidate in candidates:
        if candidate.exists() and (candidate / 'eng.traineddata').exists():
            return candidate
    # Fallback: directory exists but no eng.traineddata
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def download_eng_traineddata(tessdata_dir: Path) -> bool:
    """Download eng.traineddata into the tessdata directory."""
    dest = tessdata_dir / 'eng.traineddata'
    print(f'  Downloading eng.traineddata (~4 MB)...')
    try:
        def progress(block_count, block_size, total_size):
            if total_size > 0:
                pct = min(100, block_count * block_size * 100 // total_size)
                print(f'\r    {pct}%', end='', flush=True)
        urllib.request.urlretrieve(ENG_TRAINEDDATA_URL, dest, reporthook=progress)
        print(f'\r  Downloaded eng.traineddata to {tessdata_dir}')
        return True
    except Exception as e:
        print(f'\n  [!] Failed to download eng.traineddata: {e}')
        print(f'      Download manually from: {ENG_TRAINEDDATA_URL}')
        print(f'      Place in: {tessdata_dir}')
        return False


def configure_tesseract_path(tesseract_dir: Path):
    """Configure PATH and TESSDATA_PREFIX for Tesseract."""
    tesseract_dir = Path(tesseract_dir)
    dir_str = str(tesseract_dir)

    print(f'\n  Configuring Tesseract at: {dir_str}')

    # Add to User PATH
    add_to_user_path(dir_str)

    # Set TESSDATA_PREFIX — search multiple locations
    tessdata = find_tessdata(tesseract_dir)
    if tessdata:
        set_user_env_var('TESSDATA_PREFIX', str(tessdata))
        os.environ['TESSDATA_PREFIX'] = str(tessdata)
        print(f'  Set TESSDATA_PREFIX = {tessdata}')
        if not (tessdata / 'eng.traineddata').exists():
            download_eng_traineddata(tessdata)
    else:
        # tessdata directory doesn't exist at all — create it and download
        tessdata = tesseract_dir / 'tessdata'
        tessdata.mkdir(exist_ok=True)
        set_user_env_var('TESSDATA_PREFIX', str(tessdata))
        os.environ['TESSDATA_PREFIX'] = str(tessdata)
        print(f'  Created {tessdata}')
        print(f'  Set TESSDATA_PREFIX = {tessdata}')
        download_eng_traineddata(tessdata)

    # Also set for current process
    os.environ['PATH'] = dir_str + ';' + os.environ.get('PATH', '')

    # Verify
    try:
        result = subprocess.run(
            [str(tesseract_dir / 'tesseract.exe'), '--version'],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            version = result.stdout.splitlines()[0] if result.stdout else 'unknown'
            print(f'  Tesseract version: {version}')
            print(f'\n  NOTE: Restart your terminal for PATH changes to take effect.')
        else:
            print(f'  [!] Could not verify Tesseract. Check the installation.')
    except (FileNotFoundError, OSError):
        print(f'  [!] Could not run tesseract.exe at {tesseract_dir}. Check the installation.')


# ── Ontology Downloads ───────────────────────────────────────────────────────

DOWNLOADS = [
    (
        'hgnc_complete_set.tsv',
        'https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt',
        '~16 MB',
    ),
    (
        'mondo.json',
        'https://github.com/monarch-initiative/mondo/releases/latest/download/mondo.json',
        '~99 MB',
    ),
]


def download_file(filename: str, url: str, size_hint: str, force: bool):
    dest = DATA_DIR / filename
    if dest.exists() and not force:
        print(f'  {filename} already exists — skipping (use --update-entities to re-download).')
        return

    print(f'\n  Downloading {filename} ({size_hint})...')

    def progress(block_count, block_size, total_size):
        if total_size > 0:
            pct = min(100, block_count * block_size * 100 // total_size)
            print(f'\r    {pct}%', end='', flush=True)

    try:
        _, headers = urllib.request.urlretrieve(url, dest, reporthook=progress)
        actual_size = dest.stat().st_size
        # Verify download wasn't truncated (check Content-Length if server provided it)
        expected_size = headers.get('Content-Length')
        if expected_size and int(expected_size) > actual_size:
            print(f'\n  ERROR: {filename} download truncated '
                  f'({actual_size // (1024*1024)} MB of {int(expected_size) // (1024*1024)} MB)',
                  file=sys.stderr)
            dest.unlink()
            sys.exit(1)
        # Sanity check: JSON files should be parseable
        if filename.endswith('.json') and actual_size > 0:
            import json as _json
            try:
                with open(dest, encoding='utf-8') as f:
                    _json.load(f)
            except _json.JSONDecodeError as je:
                print(f'\n  ERROR: {filename} is not valid JSON (likely truncated download)',
                      file=sys.stderr)
                print(f'      {je}', file=sys.stderr)
                dest.unlink()
                sys.exit(1)
        print(f'\r  Downloaded {filename} ({actual_size // (1024 * 1024):.0f} MB)')
    except Exception as e:
        print(f'\n  ERROR downloading {filename}: {e}', file=sys.stderr)
        if dest.exists():
            dest.unlink()
        sys.exit(1)


def setup_entities(force: bool):
    section('Setting up entity dictionaries')
    DATA_DIR.mkdir(exist_ok=True)

    print('\n  Downloading ontology source files...')
    for filename, url, size_hint in DOWNLOADS:
        download_file(filename, url, size_hint, force)

    print('\n  Building lookup dictionaries...')
    result = subprocess.run(
        [sys.executable, str(DATA_DIR / 'build_dictionaries.py')],
    )
    if result.returncode != 0:
        print('\n  ERROR: Dictionary build failed — check output above.', file=sys.stderr)
    else:
        print('\n  Entity dictionaries ready.')


# ── Verification ─────────────────────────────────────────────────────────────

def verify():
    section('Verifying installation')
    checks = [
        ('Core', 'import defusedxml, bs4; print("  Core packages OK")'),
        ('AI', 'import anthropic, fitz, PIL, pdfplumber, pptx, docx; print("  AI packages OK")'),
        ('Data', 'import pandas, openpyxl, tabulate, yaml; print("  Data packages OK")'),
    ]
    for name, code in checks:
        result = subprocess.run(
            [sys.executable, '-c', code],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(result.stdout.strip())
        else:
            print(f'  [!] {name} packages: some missing — {result.stderr.strip()[:120]}')

    # Functional Tesseract test (uses the same runtime path as onenote_to_obsidian)
    try:
        from PIL import Image, ImageDraw
        from vision_ai.ocr_utils import ocr_image
        img = Image.new('RGB', (200, 50), color='white')
        ImageDraw.Draw(img).text((10, 10), 'OCR test', fill='black')
        text = ocr_image(img)
        if text:
            print('  Tesseract OCR: working')
        else:
            tess_path = find_tesseract()
            if tess_path:
                print(f'  [!] Tesseract found at {tess_path} but OCR returned no text.')
                print(f'      TESSDATA_PREFIX: {os.environ.get("TESSDATA_PREFIX", "(not set)")}')
                print(f'      Ensure eng.traineddata exists in your tessdata folder.')
            else:
                print('  [!] Tesseract not found — OCR will be skipped')
                print('      Run: python setup.py --setup-tesseract')
    except Exception as e:
        print(f'  [!] Tesseract OCR test failed: {e}')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='One-stop setup for OneNote to Obsidian Sync')
    parser.add_argument(
        '--update-entities', action='store_true',
        help='Re-download ontology files and rebuild dictionaries',
    )
    parser.add_argument(
        '--skip-packages', action='store_true',
        help='Skip package installation (only set up entity dictionaries)',
    )
    parser.add_argument(
        '--setup-tesseract', action='store_true',
        help='Set up Tesseract OCR without admin rights',
    )
    parser.add_argument(
        '--tesseract-path', type=Path, default=None,
        help='Path to existing Tesseract installation folder (skips download)',
    )
    args = parser.parse_args()

    print('=' * 60)
    print('  OneNote to Obsidian — Setup')
    print('=' * 60)

    # Tesseract-only setup
    if args.setup_tesseract or args.tesseract_path:
        if args.tesseract_path:
            if (args.tesseract_path / 'tesseract.exe').exists():
                configure_tesseract_path(args.tesseract_path)
            else:
                print(f'\n  ERROR: tesseract.exe not found in {args.tesseract_path}')
                sys.exit(1)
        else:
            setup_tesseract_manual()
        verify()
        print('\n' + '=' * 60)
        print('  Tesseract setup complete.')
        print('=' * 60 + '\n')
        return

    # Full setup
    if not args.skip_packages:
        install_pip_packages()

        conda = conda_executable()
        if conda:
            install_conda_packages(conda)
        else:
            warn_no_conda()

        # Tesseract setup (same for conda and non-conda — conda tesseract
        # package doesn't provide the Windows OCR binary reliably)
        tess_path = find_tesseract()
        if tess_path:
            print(f'\n  Tesseract found at: {tess_path}')
            configure_tesseract_path(tess_path)
        else:
            print('\n  [!] Tesseract not found. Run later: python setup.py --setup-tesseract')

    setup_entities(force=args.update_entities)
    verify()

    print('\n' + '=' * 60)
    print('  Setup complete.')
    print('  Run: python onenote_to_obsidian.py')
    print('=' * 60 + '\n')


if __name__ == '__main__':
    main()

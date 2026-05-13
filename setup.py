"""One-stop setup: install dependencies, Tesseract, and entity dictionaries.

Run once after cloning the repo:
    python setup.py

Re-run to update entity ontologies (updated monthly):
    python setup.py --update-entities
"""

import argparse
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).parent / 'entity_data'

# ── Packages ──────────────────────────────────────────────────────────────────

PIP_PACKAGES = [
    'defusedxml',
    'beautifulsoup4',
    'anthropic',
    'pymupdf',
    'pillow',
    'pdfplumber',
    'python-pptx',
    'python-docx',
    'tabulate',
    'PyYAML',
]

# Installed via conda (better binary compatibility on Windows)
CONDA_PACKAGES = [
    'pandas',
    'openpyxl',
    'tesseract',
    'pytesseract',
]

# ── Ontology downloads ────────────────────────────────────────────────────────

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


def conda_executable() -> str | None:
    """Return path to conda if we are inside a conda environment."""
    conda_prefix = os.environ.get('CONDA_PREFIX')
    if not conda_prefix:
        return None
    # Try common locations
    for candidate in ['conda', os.path.join(conda_prefix, '..', '..', 'Scripts', 'conda')]:
        try:
            result = subprocess.run(
                [candidate, '--version'], capture_output=True, text=True)
            if result.returncode == 0:
                return candidate
        except FileNotFoundError:
            continue
    return None


def install_pip_packages():
    section('Installing pip packages')
    ok = run(
        [sys.executable, '-m', 'pip', 'install', '--upgrade'] + PIP_PACKAGES,
        'pip install',
    )
    if not ok:
        print('  Some pip packages failed — check output above.', file=sys.stderr)


def install_conda_packages(conda: str):
    section('Installing conda packages (pandas, openpyxl, tesseract, pytesseract)')
    run(
        [conda, 'install', '-c', 'conda-forge', '--yes'] + CONDA_PACKAGES,
        'conda install',
    )


def warn_no_conda():
    print('\n  [!] Not running inside a conda environment.')
    print('      pandas and openpyxl will be installed via pip (usually fine).')
    print('      Tesseract cannot be installed automatically — install it manually:')
    print('        conda install -c conda-forge tesseract pytesseract')
    print('        OR download from https://github.com/UB-Mannheim/tesseract/wiki')
    run(
        [sys.executable, '-m', 'pip', 'install', 'pandas', 'openpyxl'],
        'pip install pandas openpyxl',
    )


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
        urllib.request.urlretrieve(url, dest, reporthook=progress)
        print(f'\r  Downloaded {filename} ({dest.stat().st_size // (1024 * 1024):.0f} MB)')
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


def verify():
    section('Verifying installation')
    checks = [
        ('Core', 'import defusedxml, bs4; print("  Core packages OK")'),
        ('AI', 'import anthropic, fitz, PIL, pdfplumber, pptx, docx; print("  AI packages OK")'),
        ('Data', 'import pandas, openpyxl, tabulate, yaml; print("  Data packages OK")'),
        ('OCR', 'import pytesseract; print("  pytesseract OK")'),
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

    result = subprocess.run(['tesseract', '--version'], capture_output=True, text=True)
    if result.returncode == 0:
        print(f'  Tesseract: {result.stdout.splitlines()[0]}')
    else:
        print('  [!] Tesseract binary not found in PATH (OCR will be skipped)')


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
    args = parser.parse_args()

    print('=' * 60)
    print('  OneNote to Obsidian — Setup')
    print('=' * 60)

    if not args.skip_packages:
        install_pip_packages()

        conda = conda_executable()
        if conda:
            install_conda_packages(conda)
        else:
            warn_no_conda()

    setup_entities(force=args.update_entities)
    verify()

    print('\n' + '=' * 60)
    print('  Setup complete.')
    print('  Run: python onenote_to_obsidian.py')
    print('=' * 60 + '\n')


if __name__ == '__main__':
    main()

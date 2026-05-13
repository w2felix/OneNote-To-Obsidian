"""One-time setup: download ontology files and build entity dictionaries.

Downloads:
  HGNC gene nomenclature  (~16 MB)
  MONDO disease ontology  (~99 MB)

Then runs entity_data/build_dictionaries.py to produce the lookup JSONs.
Re-run whenever you want to update the ontologies (updated monthly).
"""

import subprocess
import sys
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).parent / 'entity_data'

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


def download(filename: str, url: str, size_hint: str):
    dest = DATA_DIR / filename
    if dest.exists():
        answer = input(f"  {filename} already exists. Re-download? [y/N] ").strip().lower()
        if answer != 'y':
            print(f"  Skipping {filename}.")
            return

    print(f"  Downloading {filename} ({size_hint})...")

    def progress(block_count, block_size, total_size):
        if total_size > 0:
            pct = min(100, block_count * block_size * 100 // total_size)
            print(f"\r    {pct}%", end='', flush=True)

    try:
        urllib.request.urlretrieve(url, dest, reporthook=progress)
        print(f"\r  Downloaded {filename} ({dest.stat().st_size // (1024*1024):.0f} MB)")
    except Exception as e:
        print(f"\n  ERROR downloading {filename}: {e}", file=sys.stderr)
        if dest.exists():
            dest.unlink()
        sys.exit(1)


def main():
    print('=' * 60)
    print('  Entity Dictionary Setup')
    print('=' * 60)

    DATA_DIR.mkdir(exist_ok=True)

    print('\n[Step 1] Downloading ontology source files...')
    for filename, url, size_hint in DOWNLOADS:
        download(filename, url, size_hint)

    print('\n[Step 2] Building lookup dictionaries...')
    result = subprocess.run(
        [sys.executable, str(DATA_DIR / 'build_dictionaries.py')],
        cwd=str(DATA_DIR),
    )
    if result.returncode != 0:
        print('\nERROR: Dictionary build failed.', file=sys.stderr)
        sys.exit(1)

    print('\nSetup complete. Run onenote_to_obsidian.py — entity extraction is now active.')


if __name__ == '__main__':
    main()

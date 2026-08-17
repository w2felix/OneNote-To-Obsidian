"""Interactive setup for the repo-local `.anthropic-credentials.toml`.

Detects tux / foundry / anthropic and writes a matching config. Re-runnable.

Usage:
    python configure_auth.py
    python configure_auth.py --provider tux --non-interactive
"""
from __future__ import annotations

import argparse
import sys

from vision_ai.credentials_loader import (
    CREDS_FILE, REPO_ROOT, detect_available, _winreg_get, _tux_reachable,
)

TEMPLATE = """# Anthropic credentials for this repo (gitignored).
active_provider = "{active}"

[tux]
base_url = "{tux_url}"
auth_token = "managed-by-tux"

[foundry]
base_url = "{foundry_url}"
auth_token = "{foundry_token}"

[anthropic]
api_key = "{anthropic_key}"
"""


def _prompt(msg, default=""):
    suffix = f" [{default}]" if default else ""
    v = input(f"{msg}{suffix}: ").strip()
    return v or default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["auto", "tux", "foundry", "anthropic"], default=None)
    ap.add_argument("--non-interactive", action="store_true")
    args = ap.parse_args()

    print(f"Repo root: {REPO_ROOT}")
    print(f"Credentials file: {CREDS_FILE} (exists={CREDS_FILE.exists()})\n")
    status = detect_available()
    print("Detection:")
    print(f"  tux       reachable={status['tux']['reachable']} at {status['tux']['base_url']}")
    print(f"  foundry   registry_token_present={status['foundry']['registry_token_present']}")
    print(f"  anthropic registry_key_present={status['anthropic']['registry_key_present']}\n")

    tux_url = status["tux"]["base_url"]
    foundry_url = _winreg_get("ANTHROPIC_BASE_URL") or ""
    foundry_token = _winreg_get("ANTHROPIC_AUTH_TOKEN") or ""
    anthropic_key = _winreg_get("ANTHROPIC_API_KEY") or ""

    if args.provider:
        active = args.provider
    elif args.non_interactive:
        if status["tux"]["reachable"]: active = "tux"
        elif status["foundry"]["registry_token_present"]: active = "foundry"
        elif status["anthropic"]["registry_key_present"]: active = "anthropic"
        else: active = "auto"
    else:
        default = "tux" if status["tux"]["reachable"] else "auto"
        active = _prompt("Active provider (auto/tux/foundry/anthropic)", default)
        if active in ("foundry", "auto") and not foundry_token:
            foundry_token = _prompt("Foundry auth_token (blank to skip)", "")
            foundry_url = _prompt("Foundry base_url", foundry_url or "https://palantir.mcloud.merckgroup.com/api/v2/llm/proxy/anthropic")
        if active in ("anthropic", "auto") and not anthropic_key:
            anthropic_key = _prompt("Anthropic API key (blank to skip)", "")

    body = TEMPLATE.format(
        active=active, tux_url=tux_url or "http://127.0.0.1:18080",
        foundry_url=foundry_url, foundry_token=foundry_token, anthropic_key=anthropic_key,
    )
    CREDS_FILE.write_text(body, encoding="utf-8")
    print(f"Wrote {CREDS_FILE}")
    if active == "tux" and not _tux_reachable(tux_url):
        print(f"\nNote: Tux not reachable at {tux_url}. Start Tux before running the pipeline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

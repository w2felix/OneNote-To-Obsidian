"""Per-repo Anthropic credential loader.

Reads a gitignored `.anthropic-credentials.toml` at the repo root. Supports
tux, foundry, anthropic (direct), and google_vertex (placeholder). Injects
resolved credentials into os.environ so downstream code that reads
ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY continues to work.

Detection order when active_provider = "auto":
  1. Existing process env
  2. Tux daemon reachable at its configured base_url
  3. Foundry section with non-empty auth_token
  4. Anthropic section with non-empty api_key
  5. Windows User registry (legacy fallback)
"""
from __future__ import annotations

import os
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

try:
    import tomllib  # py3.11+
except ImportError:
    tomllib = None

REPO_ROOT = Path(__file__).resolve().parents[1]
CREDS_FILE = REPO_ROOT / ".anthropic-credentials.toml"


def _read_toml() -> dict:
    if not CREDS_FILE.exists() or tomllib is None:
        return {}
    try:
        with open(CREDS_FILE, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _tux_reachable(base_url: str, timeout: float = 0.5) -> bool:
    if not base_url:
        return False
    probe = base_url.rstrip("/") + "/status"
    try:
        with urllib.request.urlopen(probe, timeout=timeout) as r:
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return False


def _winreg_get(name: str) -> Optional[str]:
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            val, _ = winreg.QueryValueEx(key, name)
        return val or None
    except (FileNotFoundError, OSError):
        return None


def _apply(base_url, auth_token, api_key):
    if base_url:
        os.environ["ANTHROPIC_BASE_URL"] = base_url
    if auth_token:
        os.environ["ANTHROPIC_AUTH_TOKEN"] = auth_token
    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key


def load_credentials() -> str:
    if os.environ.get("ANTHROPIC_AUTH_TOKEN") and os.environ.get("ANTHROPIC_BASE_URL"):
        return "env"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "env"

    cfg = _read_toml()
    active = (cfg.get("active_provider") or "auto").lower()
    order = [active] if active != "auto" else ["tux", "foundry", "anthropic"]

    for provider in order:
        section = cfg.get(provider) or {}
        if provider == "tux":
            base_url = section.get("base_url") or "http://127.0.0.1:18080"
            token = section.get("auth_token") or "managed-by-tux"
            if _tux_reachable(base_url):
                _apply(base_url, token, None)
                return "tux"
        elif provider == "foundry":
            base_url = section.get("base_url"); token = section.get("auth_token")
            if base_url and token:
                _apply(base_url, token, None); return "foundry"
        elif provider == "anthropic":
            key = section.get("api_key")
            if key:
                _apply(None, None, key); return "anthropic"

    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"):
        if not os.environ.get(name):
            v = _winreg_get(name)
            if v:
                os.environ[name] = v
    if os.environ.get("ANTHROPIC_AUTH_TOKEN") and os.environ.get("ANTHROPIC_BASE_URL"):
        return "winreg"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "winreg"
    return "none"


def detect_available() -> dict:
    cfg = _read_toml()
    tux_section = cfg.get("tux") or {}
    tux_url = tux_section.get("base_url") or "http://127.0.0.1:18080"
    fnd = cfg.get("foundry") or {}
    ant = cfg.get("anthropic") or {}
    return {
        "tux": {"reachable": _tux_reachable(tux_url), "base_url": tux_url},
        "foundry": {
            "configured": bool(fnd.get("auth_token") and fnd.get("base_url")),
            "registry_token_present": bool(_winreg_get("ANTHROPIC_AUTH_TOKEN") and _winreg_get("ANTHROPIC_BASE_URL")),
        },
        "anthropic": {
            "configured": bool(ant.get("api_key")),
            "registry_key_present": bool(_winreg_get("ANTHROPIC_API_KEY")),
        },
    }


if __name__ == "__main__":
    print(f"Credentials file: {CREDS_FILE} (exists={CREDS_FILE.exists()})")
    print(f"Active provider: {load_credentials()}")
    print(json.dumps(detect_available(), indent=2))

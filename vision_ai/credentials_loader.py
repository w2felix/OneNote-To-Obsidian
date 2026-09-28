"""Per-repo Anthropic credential loader.

Delegates to the sibling `2nd-brain` repo's
`pipeline.vault_core.credentials_loader` when that repo is present alongside
this one - one canonical `.anthropic-credentials.toml` and tux-probe
implementation for the machine, instead of a second copy that can drift out
of sync. Falls back to the local implementation below when the sibling repo
isn't importable, so this repo still works standalone (e.g. cloned without
`2nd-brain` present).

Local-fallback detection order when active_provider = "auto":
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
_SIBLING_2ND_BRAIN = REPO_ROOT.parent / "2nd-brain"


def _hub():
    """Return the sibling 2nd-brain repo's credentials_loader module, or None.

    vault_core/__init__.py does absolute `from vault_core.config import ...`,
    so `pipeline/` itself (not the 2nd-brain repo root) must be on sys.path
    for `vault_core` to resolve as a top-level package - matching how
    2nd-brain's own scripts import it (see pipeline/tools/check_vault_paths.py).
    """
    sib_pipeline = _SIBLING_2ND_BRAIN / "pipeline"
    hub_file = sib_pipeline / "vault_core" / "credentials_loader.py"
    if not hub_file.exists():
        return None
    sib_str = str(sib_pipeline)
    if sib_str not in sys.path:
        sys.path.insert(0, sib_str)
    try:
        from vault_core import credentials_loader as hub_loader
        return hub_loader
    except Exception:
        return None


def _read_toml() -> dict:
    if not CREDS_FILE.exists() or tomllib is None:
        return {}
    try:
        with open(CREDS_FILE, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _tux_reachable(base_url: str, timeout: float = 3.0) -> bool:
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


def _load_credentials_local() -> str:
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


def _detect_available_local() -> dict:
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


def load_credentials() -> str:
    """Resolve credentials and inject into os.environ. Returns provider name used.

    Delegates to the sibling 2nd-brain repo's loader when present, so both
    repos read the same .anthropic-credentials.toml and share one
    tux-reachability implementation. Falls back to the local implementation
    above when that repo isn't available.
    """
    hub = _hub()
    if hub is not None:
        try:
            return hub.load_credentials()
        except Exception:
            pass
    return _load_credentials_local()


def detect_available() -> dict:
    hub = _hub()
    if hub is not None:
        try:
            return hub.detect_available()
        except Exception:
            pass
    return _detect_available_local()


if __name__ == "__main__":
    print(f"Credentials file: {CREDS_FILE} (exists={CREDS_FILE.exists()})")
    print(f"Sibling 2nd-brain hub available: {_hub() is not None}")
    print(f"Active provider: {load_credentials()}")
    print(json.dumps(detect_available(), indent=2))

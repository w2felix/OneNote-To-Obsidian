"""Anthropic API client singleton with credential loading and retry logic."""

import os
import sys
import time
import logging
from pathlib import Path
from typing import Optional

from vision_ai import rate_limiter

logger = logging.getLogger(__name__)

_client = None


def _sibling_model_defaults():
    """Read model ID defaults from the sibling 2nd-brain repo's vault_core.config,
    if that repo is present, so both repos track the same model IDs instead of
    drifting independently. Returns (sonnet, haiku) or (None, None).

    vault_core/__init__.py does absolute `from vault_core.config import ...`,
    so `pipeline/` itself (not the 2nd-brain repo root) must be on sys.path
    for `vault_core` to resolve as a top-level package.
    """
    sib_pipeline = Path(__file__).resolve().parents[2] / "2nd-brain" / "pipeline"
    if not (sib_pipeline / "vault_core" / "config.py").exists():
        return None, None
    sib_str = str(sib_pipeline)
    if sib_str not in sys.path:
        sys.path.insert(0, sib_str)
    try:
        from vault_core import config as hub_config
        return hub_config.MODEL_SONNET, hub_config.MODEL_HAIKU
    except Exception:
        return None, None


_default_sonnet, _default_haiku = _sibling_model_defaults()

# Model names - env vars take precedence, then the sibling repo's config,
# then these literals for standalone use without 2nd-brain present.
VISION_MODEL = os.environ.get("VISION_MODEL") or _default_sonnet or "claude-sonnet-5"
TAGGER_MODEL = os.environ.get("TAGGER_MODEL") or _default_haiku or "claude-haiku-4-5-20251001"

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0
RETRYABLE_STATUS_CODES = {429, 503, 529}

# SDK requires non-empty api_key but the Foundry proxy authenticates via
# Authorization: Bearer only; this value is never sent to the upstream.
_PROXY_API_KEY_PLACEHOLDER = 'proxy-auth-via-bearer'


def _load_credentials():
    if os.environ.get('ANTHROPIC_AUTH_TOKEN') and os.environ.get('ANTHROPIC_BASE_URL'):
        return
    if os.environ.get('ANTHROPIC_API_KEY'):
        return
    try:
        from vision_ai.credentials_loader import load_credentials as _load_from_toml
        _load_from_toml()
        if os.environ.get('ANTHROPIC_AUTH_TOKEN') and os.environ.get('ANTHROPIC_BASE_URL'):
            return
        if os.environ.get('ANTHROPIC_API_KEY'):
            return
    except Exception:
        pass
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment', 0, winreg.KEY_READ)
        try:
            for var in ('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY'):
                if not os.environ.get(var):
                    try:
                        val, _ = winreg.QueryValueEx(key, var)
                        if val and len(str(val)) > 5:
                            os.environ[var] = str(val)
                    except FileNotFoundError:
                        pass
        finally:
            winreg.CloseKey(key)
    except (ImportError, OSError):
        pass


def get_client():
    global _client
    if _client is not None:
        return _client

    _load_credentials()

    import anthropic

    auth_token = os.environ.get('ANTHROPIC_AUTH_TOKEN')
    base_url = os.environ.get('ANTHROPIC_BASE_URL')
    api_key = os.environ.get('ANTHROPIC_API_KEY')

    if auth_token and base_url:
        _client = anthropic.Anthropic(
            api_key=_PROXY_API_KEY_PLACEHOLDER,
            base_url=base_url,
            default_headers={
                'Authorization': f'Bearer {auth_token}',
                'x-api-key': '',
            },
        )
        logger.debug("Vision AI: using Foundry proxy (%s) with Bearer auth", base_url)
    elif api_key:
        _client = anthropic.Anthropic(api_key=api_key)
        logger.debug("Vision AI: using direct Anthropic API")
    else:
        raise RuntimeError(
            "No Anthropic credentials found. Set ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL "
            "(Foundry proxy) or ANTHROPIC_API_KEY in environment or Windows User variables."
        )

    return _client


def api_call_with_retry(messages: list, system: str = "", max_tokens: int = 4096,
                        model: Optional[str] = None,
                        return_stop_reason: bool = False) -> "str | tuple[str, str]":
    client = get_client()
    model = model or VISION_MODEL

    kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system

    # Apply rate limiting before making the API call
    rate_limiter.wait_if_needed()

    for attempt in range(RETRY_ATTEMPTS):
        try:
            response = client.messages.create(**kwargs)
            if not response.content:
                raise RuntimeError(f"Empty content in API response (stop_reason={response.stop_reason})")
            text = response.content[0].text
            if return_stop_reason:
                return text, response.stop_reason
            return text
        except Exception as e:
            status = getattr(e, 'status_code', None)
            if status in RETRYABLE_STATUS_CODES and attempt < RETRY_ATTEMPTS - 1:
                # Check for Retry-After header (Anthropic API returns this on 429)
                retry_after = None
                if hasattr(e, 'response') and e.response:
                    headers = getattr(e.response, 'headers', {})
                    retry_after_str = headers.get('retry-after') or headers.get('Retry-After')
                    if retry_after_str:
                        try:
                            retry_after = int(retry_after_str)
                        except (ValueError, TypeError):
                            pass

                if retry_after:
                    delay = retry_after
                    logger.warning(f"API call failed ({status}), retrying in {delay}s (from Retry-After header)")
                else:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(f"API call failed ({status}), retrying in {delay}s (exponential backoff)")

                time.sleep(delay)
                continue
            raise

"""Anthropic API client singleton with credential loading and retry logic."""

import os
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_client = None

VISION_MODEL = "claude-sonnet-4-6"
TAGGER_MODEL = "claude-haiku-4-5-20251001"
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0
RETRYABLE_STATUS_CODES = {429, 503, 529}


def _load_credentials():
    if os.environ.get('ANTHROPIC_AUTH_TOKEN') and os.environ.get('ANTHROPIC_BASE_URL'):
        return
    if os.environ.get('ANTHROPIC_API_KEY'):
        return
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
            api_key=auth_token,
            base_url=base_url,
        )
        logger.debug("Vision AI: credentials loaded from environment")
    elif api_key:
        _client = anthropic.Anthropic(api_key=api_key)
        logger.debug("Vision AI: credentials loaded from environment")
    else:
        raise RuntimeError(
            "No Anthropic credentials found. Set ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL "
            "(Foundry proxy) or ANTHROPIC_API_KEY in environment or Windows User variables."
        )

    return _client


def api_call_with_retry(messages: list, system: str = "", max_tokens: int = 4096,
                        model: Optional[str] = None) -> str:
    client = get_client()
    model = model or VISION_MODEL

    kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system

    for attempt in range(RETRY_ATTEMPTS):
        try:
            response = client.messages.create(**kwargs)
            return response.content[0].text
        except Exception as e:
            status = getattr(e, 'status_code', None)
            if status in RETRYABLE_STATUS_CODES and attempt < RETRY_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(f"API call failed ({status}), retrying in {delay}s...")
                time.sleep(delay)
                continue
            raise

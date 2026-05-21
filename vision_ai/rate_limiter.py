"""Global API rate limiter to prevent hitting Anthropic's rate limits.

This module implements a simple sliding window rate limiter that tracks
API call timestamps and sleeps when necessary to stay under the limit.

Configuration:
    Set RATE_LIMIT_ENABLED=0 to disable rate limiting entirely.
    Set RATE_LIMIT_RPM to configure requests per minute (default: 50).
"""

import os
import time
import logging
from collections import deque
from threading import Lock

logger = logging.getLogger(__name__)

# Default limits - can be adjusted based on Anthropic tier or environment
DEFAULT_REQUESTS_PER_MINUTE = int(os.environ.get("RATE_LIMIT_RPM", "50"))
DEFAULT_WINDOW_SECONDS = 60
RATE_LIMITING_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "1") != "0"

_call_timestamps = deque()
_lock = Lock()
_requests_per_minute = DEFAULT_REQUESTS_PER_MINUTE
_window_seconds = DEFAULT_WINDOW_SECONDS


def configure(requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
              window_seconds: int = DEFAULT_WINDOW_SECONDS):
    """Configure rate limiter parameters.

    Args:
        requests_per_minute: Maximum requests allowed per window
        window_seconds: Size of sliding window in seconds
    """
    global _requests_per_minute, _window_seconds
    _requests_per_minute = requests_per_minute
    _window_seconds = window_seconds
    logger.debug(f"Rate limiter configured: {requests_per_minute} req/{window_seconds}s")


def wait_if_needed():
    """Check if we're approaching rate limit and sleep if needed.

    This implements a sliding window rate limiter. If we've made
    `requests_per_minute` calls in the last `window_seconds`, we'll
    sleep until the oldest call falls outside the window.

    Can be disabled via RATE_LIMIT_ENABLED=0 environment variable.

    Thread-safe via lock.
    """
    if not RATE_LIMITING_ENABLED:
        return

    with _lock:
        now = time.time()

        # Remove timestamps outside the current window
        while _call_timestamps and now - _call_timestamps[0] >= _window_seconds:
            _call_timestamps.popleft()

        # Check if we need to wait
        if len(_call_timestamps) >= _requests_per_minute:
            oldest = _call_timestamps[0]
            sleep_time = _window_seconds - (now - oldest) + 0.1  # +0.1s buffer
            if sleep_time > 0:
                logger.debug(f"Rate limit: {len(_call_timestamps)} calls in window, "
                           f"sleeping {sleep_time:.1f}s")
                time.sleep(sleep_time)
                now = time.time()

        # Record this call
        _call_timestamps.append(now)


def reset():
    """Clear all tracked timestamps. Useful for testing."""
    with _lock:
        _call_timestamps.clear()

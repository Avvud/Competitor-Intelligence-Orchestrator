"""
rate_limiter.py — simple token-bucket rate limiter for Groq calls.

Features:
- Limits to N requests per minute (configurable)
- Reads Retry-After header on 429 and waits that long
- Exponential backoff with jitter for other errors
- Uses time.monotonic() — can be patched in tests
"""

import logging
import random
import time

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, requests_per_minute: int = 25):
        self.rpm = requests_per_minute
        self._min_gap = 60.0 / requests_per_minute  # seconds between calls
        self._last_call_time = 0.0

    def wait_if_needed(self):
        """Block until we are allowed to make the next request."""
        now = time.monotonic()
        elapsed = now - self._last_call_time
        if elapsed < self._min_gap:
            sleep_for = self._min_gap - elapsed
            logger.debug("Rate limiter sleeping %.2fs", sleep_for)
            time.sleep(sleep_for)
        self._last_call_time = time.monotonic()

    def handle_retry_after(self, retry_after_seconds: float):
        """Called when we get a 429 with a Retry-After header."""
        logger.warning("Groq 429 — waiting %.0fs (Retry-After)", retry_after_seconds)
        time.sleep(retry_after_seconds)
        self._last_call_time = time.monotonic()


def backoff_sleep(attempt: int, base: float = 1.0, max_wait: float = 60.0):
    """
    Exponential backoff with jitter.
    attempt=0 → ~1s, attempt=1 → ~2s, attempt=2 → ~4s, …
    """
    wait = min(base * (2 ** attempt), max_wait)
    jitter = random.uniform(0, wait * 0.2)   # ±20% jitter
    total = wait + jitter
    logger.debug("Backoff attempt %d, sleeping %.2fs", attempt, total)
    time.sleep(total)

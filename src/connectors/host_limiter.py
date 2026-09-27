"""
host_limiter.py — per-host rate limiter for all connectors.

Each hostname gets its own token bucket. Connectors call
wait_for_host(hostname, delay_s) before making any HTTP request.

Thread-safe (uses threading.Lock). Time is mockable via time.monotonic.
"""

import threading
import time
from collections import defaultdict


class HostRateLimiter:
    """
    Simple per-host rate limiter.
    Stores the timestamp of the last request to each host.
    Sleeps if the required delay has not yet elapsed.
    """

    def __init__(self):
        self._last_request: dict[str, float] = defaultdict(lambda: 0.0)
        self._lock = threading.Lock()

    def wait_for_host(self, hostname: str, delay_s: float) -> None:
        """
        Block until at least delay_s seconds have passed since the last
        request to this hostname, then record the new timestamp.
        """
        with self._lock:
            now = time.monotonic()
            last = self._last_request[hostname]
            elapsed = now - last
            if elapsed < delay_s:
                sleep_for = delay_s - elapsed
                time.sleep(sleep_for)
            self._last_request[hostname] = time.monotonic()

    def last_request_time(self, hostname: str) -> float:
        """Return the monotonic timestamp of the last request to hostname."""
        with self._lock:
            return self._last_request[hostname]


# Module-level singleton — shared across all connector instances
_global_limiter = HostRateLimiter()


def wait_for_host(hostname: str, delay_s: float) -> None:
    _global_limiter.wait_for_host(hostname, delay_s)


def last_request_time(hostname: str) -> float:
    return _global_limiter.last_request_time(hostname)

"""
base.py — shared contract for all connectors.

Every connector must:
  1. Subclass BaseConnector
  2. Implement fetch(competitor_id, url, **kwargs) → ConnectorRecord
  3. Return a ConnectorRecord with status one of:
       ok | empty | unavailable | quota_exhausted | blocked
  4. Never raise — all errors must be caught and returned as status

PolicyGuard: raises PolicyError if anyone tries to register a scraper
for a blocked domain (linkedin.com, instagram.com, facebook.com, x.com).
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar


# ---------------------------------------------------------------------------
# Blocked domains (scraping is banned by ToS / policy)
# ---------------------------------------------------------------------------

BLOCKED_DOMAINS = frozenset({
    "linkedin.com",
    "instagram.com",
    "facebook.com",
    "x.com",
    "twitter.com",
})


class PolicyError(Exception):
    """Raised when a connector tries to scrape a blocked domain."""


def check_policy(url: str) -> None:
    """Raise PolicyError if the URL targets a blocked domain."""
    import re
    domain = re.sub(r"https?://", "", url.lower()).split("/")[0]
    # Strip subdomains: api.linkedin.com → linkedin.com
    parts = domain.split(".")
    root = ".".join(parts[-2:]) if len(parts) >= 2 else domain
    if root in BLOCKED_DOMAINS:
        raise PolicyError(
            f"Scraping {root} is blocked by policy (ToS). "
            f"Use the manual_entry connector instead."
        )


# ---------------------------------------------------------------------------
# Shared record type returned by every connector
# ---------------------------------------------------------------------------

@dataclass
class ConnectorRecord:
    """Shared output record. All connectors return this exact shape."""
    connector:     str                     # name of the connector
    company_id:    str
    competitor_id: str
    source_url:    str
    fetched_at:    datetime = field(default_factory=datetime.utcnow)
    status:        str = "ok"             # ok|empty|unavailable|quota_exhausted|blocked
    content_hash:  str | None = None
    raw_data:      dict = field(default_factory=dict)
    version:       int = 1
    error_message: str | None = None


def content_hash(data: str | bytes) -> str:
    """SHA-256 of content, first 16 hex chars."""
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Base connector class
# ---------------------------------------------------------------------------

class BaseConnector:
    """
    All connectors inherit this. Subclasses implement _fetch().
    The public fetch() method wraps _fetch() with error handling.
    """

    name: ClassVar[str] = "base"

    def fetch(
        self,
        company_id: str,
        competitor_id: str,
        url: str,
        **kwargs,
    ) -> ConnectorRecord:
        """
        Public fetch method. Catches all exceptions and returns a
        ConnectorRecord with status="blocked" or "unavailable" on failure.
        """
        try:
            check_policy(url)
            return self._fetch(company_id, competitor_id, url, **kwargs)
        except PolicyError as exc:
            return ConnectorRecord(
                connector=self.name,
                company_id=company_id,
                competitor_id=competitor_id,
                source_url=url,
                status="blocked",
                error_message=str(exc),
            )
        except Exception as exc:
            return ConnectorRecord(
                connector=self.name,
                company_id=company_id,
                competitor_id=competitor_id,
                source_url=url,
                status="unavailable",
                error_message=str(exc),
            )

    def _fetch(
        self,
        company_id: str,
        competitor_id: str,
        url: str,
        **kwargs,
    ) -> ConnectorRecord:
        raise NotImplementedError

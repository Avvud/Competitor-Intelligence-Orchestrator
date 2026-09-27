"""
website.py — fetches the competitor's public website.

Uses ETag / content-hash caching:
  - If ETag matches the stored value, returns status="empty" (unchanged).
  - If content hash matches, skips LLM re-parse and returns status="empty".
  - On content change, bumps version and returns status="ok".
"""

import logging
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from src.connectors.base import BaseConnector, ConnectorRecord, content_hash
from src.connectors.host_limiter import wait_for_host

logger = logging.getLogger(__name__)

USER_AGENT = "CompetitorIntelBot/1.0 (research)"


class WebsiteConnector(BaseConnector):
    name = "website"

    def __init__(self, delay_s: float = 2.0):
        self.delay_s = delay_s

    def _fetch(
        self,
        company_id: str,
        competitor_id: str,
        url: str,
        prev_etag: str | None = None,
        prev_hash: str | None = None,
        prev_version: int = 0,
        **kwargs,
    ) -> ConnectorRecord:
        hostname = urlparse(url).hostname or url
        wait_for_host(hostname, self.delay_s)

        headers = {"User-Agent": USER_AGENT}
        if prev_etag:
            headers["If-None-Match"] = prev_etag

        try:
            with httpx.Client(follow_redirects=True, timeout=15.0) as client:
                resp = client.get(url, headers=headers)

                # 304 Not Modified
                if resp.status_code == 304:
                    return ConnectorRecord(
                        connector=self.name,
                        company_id=company_id,
                        competitor_id=competitor_id,
                        source_url=url,
                        status="empty",
                        content_hash=prev_hash,
                        raw_data={"reason": "ETag match — content unchanged"},
                    )

                resp.raise_for_status()

                html = resp.text
                chash = content_hash(html)

                # Content hash match — unchanged
                if prev_hash and chash == prev_hash:
                    return ConnectorRecord(
                        connector=self.name,
                        company_id=company_id,
                        competitor_id=competitor_id,
                        source_url=url,
                        status="empty",
                        content_hash=chash,
                        raw_data={"reason": "hash match — content unchanged"},
                    )

                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style"]):
                    tag.decompose()
                clean_text = soup.get_text(separator=" ", strip=True)[:8000]

                etag = resp.headers.get("etag")

                return ConnectorRecord(
                    connector=self.name,
                    company_id=company_id,
                    competitor_id=competitor_id,
                    source_url=url,
                    status="ok",
                    content_hash=chash,
                    version=prev_version + 1,
                    raw_data={
                        "html_length": len(html),
                        "clean_text": clean_text,
                        "etag": etag,
                    },
                )

        except httpx.HTTPStatusError as exc:
            sc = exc.response.status_code
            status = "unavailable" if sc in (403, 404, 410) else "unavailable"
            return ConnectorRecord(
                connector=self.name,
                company_id=company_id,
                competitor_id=competitor_id,
                source_url=url,
                status=status,
                error_message=f"HTTP {sc}",
            )

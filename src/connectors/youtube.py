"""
youtube.py — YouTube Data API v3 connector (official API only, no scraping).

Fetches the channel's recent videos and basic stats.
On quota exhaustion (403 with reason=quotaExceeded), returns status="quota_exhausted".
The pipeline continues with other connectors — this is not a crash.

Requires: YOUTUBE_API_KEY env var.
"""

import logging
import os
from urllib.parse import urlparse

import httpx

from src.connectors.base import BaseConnector, ConnectorRecord, content_hash
from src.connectors.host_limiter import wait_for_host

logger = logging.getLogger(__name__)

_YT_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeConnector(BaseConnector):
    name = "youtube"

    def __init__(self, delay_s: float = 1.0):
        self.delay_s = delay_s

    def _fetch(
        self,
        company_id: str,
        competitor_id: str,
        url: str,           # YouTube channel URL or channel_id
        prev_hash: str | None = None,
        **kwargs,
    ) -> ConnectorRecord:
        api_key = os.environ.get("YOUTUBE_API_KEY", "")
        if not api_key:
            return ConnectorRecord(
                connector=self.name,
                company_id=company_id,
                competitor_id=competitor_id,
                source_url=url,
                status="unavailable",
                error_message="YOUTUBE_API_KEY not set",
            )

        channel_id = _extract_channel_id(url)
        if not channel_id:
            return ConnectorRecord(
                connector=self.name,
                company_id=company_id,
                competitor_id=competitor_id,
                source_url=url,
                status="unavailable",
                error_message=f"Cannot extract channel_id from: {url}",
            )

        wait_for_host("www.googleapis.com", self.delay_s)

        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(
                    f"{_YT_BASE}/channels",
                    params={
                        "part": "snippet,statistics",
                        "id": channel_id,
                        "key": api_key,
                    }
                )

                # Quota exhausted
                if resp.status_code == 403:
                    try:
                        err = resp.json()
                        errors = err.get("error", {}).get("errors", [])
                        reason = errors[0].get("reason", "") if errors else ""
                    except Exception:
                        reason = ""
                    if reason == "quotaExceeded" or "quota" in resp.text.lower():
                        return ConnectorRecord(
                            connector=self.name,
                            company_id=company_id,
                            competitor_id=competitor_id,
                            source_url=url,
                            status="quota_exhausted",
                            error_message="YouTube API daily quota exceeded",
                        )

                resp.raise_for_status()
                data = resp.json()
                chash = content_hash(resp.text)

                if prev_hash and chash == prev_hash:
                    return ConnectorRecord(
                        connector=self.name,
                        company_id=company_id,
                        competitor_id=competitor_id,
                        source_url=url,
                        status="empty",
                        content_hash=chash,
                    )

                items = data.get("items", [])
                if not items:
                    return ConnectorRecord(
                        connector=self.name,
                        company_id=company_id,
                        competitor_id=competitor_id,
                        source_url=url,
                        status="empty",
                        content_hash=chash,
                    )

                return ConnectorRecord(
                    connector=self.name,
                    company_id=company_id,
                    competitor_id=competitor_id,
                    source_url=url,
                    status="ok",
                    content_hash=chash,
                    raw_data={"channel": items[0]},
                )

        except httpx.HTTPStatusError as exc:
            return ConnectorRecord(
                connector=self.name,
                company_id=company_id,
                competitor_id=competitor_id,
                source_url=url,
                status="unavailable",
                error_message=f"HTTP {exc.response.status_code}",
            )


def _extract_channel_id(url: str) -> str | None:
    """
    Extract the YouTube channel ID from a URL or return as-is if it looks
    like a channel ID (starts with UC).
    """
    if url.startswith("UC") and len(url) == 24:
        return url
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = path.split("/")
    for i, part in enumerate(parts):
        if part == "channel" and i + 1 < len(parts):
            return parts[i + 1]
    return None

"""
news_rss.py — fetches news via RSS feed URL for a competitor.

Parses standard RSS 2.0 / Atom feeds. Hash-based dedup to detect changes.
"""

import logging
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx

from src.connectors.base import BaseConnector, ConnectorRecord, content_hash
from src.connectors.host_limiter import wait_for_host

logger = logging.getLogger(__name__)


class NewsRSSConnector(BaseConnector):
    name = "news_rss"

    def __init__(self, delay_s: float = 1.0):
        self.delay_s = delay_s

    def _fetch(
        self,
        company_id: str,
        competitor_id: str,
        url: str,
        prev_hash: str | None = None,
        **kwargs,
    ) -> ConnectorRecord:
        hostname = urlparse(url).hostname or url
        wait_for_host(hostname, self.delay_s)

        try:
            with httpx.Client(follow_redirects=True, timeout=15.0) as client:
                resp = client.get(url)
                resp.raise_for_status()

            chash = content_hash(resp.text)

            # Unchanged
            if prev_hash and chash == prev_hash:
                return ConnectorRecord(
                    connector=self.name,
                    company_id=company_id,
                    competitor_id=competitor_id,
                    source_url=url,
                    status="empty",
                    content_hash=chash,
                )

            items = _parse_feed(resp.text)
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
                raw_data={"items": items[:50]},
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


def _parse_feed(text: str) -> list[dict]:
    """Parse RSS 2.0 or Atom feed, return list of {title, link, published}."""
    items = []
    try:
        root = ET.fromstring(text)
        # RSS 2.0
        channel = root.find("channel")
        if channel is not None:
            for item in channel.findall("item"):
                t = item.findtext("title", "")
                l = item.findtext("link", "")
                d = item.findtext("pubDate", "")
                items.append({"title": t, "link": l, "published": d})
            return items
        # Atom
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns):
            t = entry.findtext("a:title", "", ns)
            link_el = entry.find("a:link", ns)
            l = link_el.get("href", "") if link_el is not None else ""
            d = entry.findtext("a:published", "", ns)
            items.append({"title": t, "link": l, "published": d})
    except ET.ParseError as exc:
        logger.warning("RSS parse error: %s", exc)
    return items

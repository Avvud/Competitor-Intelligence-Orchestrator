"""
instagram_bd.py — Instagram Business Discovery API connector.

This uses the official Facebook Graph API Business Discovery endpoint.
Requires:
  - Your own Instagram Business account
  - A Facebook App with instagram_manage_insights permission
  - INSTAGRAM_ACCESS_TOKEN env var

Without the token or permission, returns status="unavailable" and
suggests using manual_entry instead. Never scrapes instagram.com.
"""

import logging
import os

import httpx

from src.connectors.base import BaseConnector, ConnectorRecord, content_hash, PolicyError

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.facebook.com/v19.0"


class InstagramBDConnector(BaseConnector):
    name = "instagram_bd"

    def __init__(self, delay_s: float = 2.0):
        self.delay_s = delay_s

    def _fetch(
        self,
        company_id: str,
        competitor_id: str,
        url: str,           # Instagram username or @handle
        prev_hash: str | None = None,
        **kwargs,
    ) -> ConnectorRecord:
        token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
        if not token:
            return ConnectorRecord(
                connector=self.name,
                company_id=company_id,
                competitor_id=competitor_id,
                source_url=url,
                status="unavailable",
                error_message=(
                    "INSTAGRAM_ACCESS_TOKEN not set. "
                    "Use manual_entry connector to import Instagram data manually."
                ),
            )

        username = _extract_username(url)
        if not username:
            return ConnectorRecord(
                connector=self.name,
                company_id=company_id,
                competitor_id=competitor_id,
                source_url=url,
                status="unavailable",
                error_message=f"Cannot extract Instagram username from: {url}",
            )

        from src.connectors.host_limiter import wait_for_host
        wait_for_host("graph.facebook.com", self.delay_s)

        try:
            with httpx.Client(timeout=15.0) as client:
                # Business Discovery: look up another account via our own account
                resp = client.get(
                    f"{_GRAPH_BASE}/me",
                    params={
                        "fields": f"business_discovery.fields(name,followers_count,media_count)"
                                  f"{{username={username}}}",
                        "access_token": token,
                    }
                )

                if resp.status_code == 403 or resp.status_code == 400:
                    err_msg = ""
                    try:
                        err_msg = resp.json().get("error", {}).get("message", "")
                    except Exception:
                        pass
                    return ConnectorRecord(
                        connector=self.name,
                        company_id=company_id,
                        competitor_id=competitor_id,
                        source_url=url,
                        status="unavailable",
                        error_message=(
                            f"Instagram Business Discovery permission denied: {err_msg}. "
                            "Use manual_entry connector instead."
                        ),
                    )

                resp.raise_for_status()
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

                data = resp.json().get("business_discovery", {})
                return ConnectorRecord(
                    connector=self.name,
                    company_id=company_id,
                    competitor_id=competitor_id,
                    source_url=url,
                    status="ok",
                    content_hash=chash,
                    raw_data={"instagram": data},
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


def _extract_username(url: str) -> str | None:
    if url.startswith("@"):
        return url[1:]
    if "instagram.com" in url:
        parts = url.rstrip("/").split("/")
        return parts[-1] if parts else None
    # Treat bare strings as usernames
    if "/" not in url and "." not in url:
        return url
    return None

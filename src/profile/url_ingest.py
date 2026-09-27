"""
url_ingest.py — fetches pages from a company website.

What it does:
  1. Fetches and parses robots.txt for the domain
  2. Tries a standard set of pages (home, about, contact, pricing, etc.)
  3. Skips any URL disallowed by robots.txt (logs the skip)
  4. Strips HTML to clean text
  5. Saves each page as a PageSnapshot in the DB (with content hash)
  6. Returns list of PageSnapshot objects for the profile extractor

Respects robots.txt. Rate-limits to 1 request/second by default.
Treats all page content as untrusted.
"""

import hashlib
import logging
import time
import uuid
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from src.core.db import PageSnapshot

logger = logging.getLogger(__name__)

USER_AGENT = "CompetitorIntelBot/1.0 (research tool; not for commercial scraping)"

# Pages to try fetching from any company site
STANDARD_PATHS = [
    "/",
    "/about",
    "/about-us",
    "/contact",
    "/contact-us",
    "/products",
    "/services",
    "/pricing",
    "/careers",
    "/jobs",
]


def _get_robots(base_url: str, client: httpx.Client) -> RobotFileParser:
    """Fetch and parse robots.txt. Returns a permissive parser on failure."""
    parser = RobotFileParser()
    robots_url = urljoin(base_url, "/robots.txt")
    try:
        resp = client.get(robots_url, timeout=10.0)
        if resp.status_code == 200 and "text" in resp.headers.get("content-type", ""):
            parser.parse(resp.text.splitlines())
            logger.debug("robots.txt fetched from %s", robots_url)
        else:
            parser.allow_all = True
            logger.debug("No robots.txt at %s (status %d)", robots_url, resp.status_code)
    except Exception as exc:
        parser.allow_all = True
        logger.warning("Could not fetch robots.txt: %s", exc)
    return parser


def _html_to_text(html: str) -> str:
    """Strip HTML tags and return clean plain text."""
    soup = BeautifulSoup(html, "html.parser")
    # Remove script and style blocks
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def fetch_pages(
    base_url: str,
    company_id: str,
    session: Session,
    delay_s: float = 1.0,
    extra_paths: list[str] | None = None,
) -> list[PageSnapshot]:
    """
    Fetch STANDARD_PATHS (plus any extra_paths) from base_url.
    Skips pages disallowed by robots.txt.
    Returns a list of PageSnapshot rows (already saved to DB).
    """
    # Normalise base URL (strip trailing slash)
    base_url = base_url.rstrip("/")
    parsed = urlparse(base_url)
    if not parsed.scheme:
        base_url = "https://" + base_url

    paths = STANDARD_PATHS + (extra_paths or [])
    snapshots: list[PageSnapshot] = []

    headers = {"User-Agent": USER_AGENT}

    with httpx.Client(headers=headers, follow_redirects=True, timeout=15.0) as client:
        robots = _get_robots(base_url, client)

        for path in paths:
            url = urljoin(base_url, path)

            # Respect robots.txt
            if not robots.can_fetch(USER_AGENT, url):
                logger.info("robots.txt disallows %s — skipping", url)
                continue

            try:
                time.sleep(delay_s)
                resp = client.get(url)

                if resp.status_code == 404:
                    logger.debug("404 at %s — skipping", url)
                    continue

                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")
                if "html" not in content_type:
                    logger.debug("Non-HTML at %s (%s) — skipping", url, content_type)
                    continue

                html = resp.text
                clean = _html_to_text(html)
                chash = _content_hash(clean)

                # Check if we already have this exact content
                existing = (
                    session.query(PageSnapshot)
                    .filter_by(company_id=company_id, url=url, content_hash=chash)
                    .first()
                )
                if existing:
                    logger.debug("Unchanged content at %s — reusing snapshot", url)
                    snapshots.append(existing)
                    continue

                snap = PageSnapshot(
                    id=str(uuid.uuid4()),
                    company_id=company_id,
                    url=url,
                    content_hash=chash,
                    raw_html=html,
                    clean_text=clean,
                    version=1,
                )
                session.add(snap)
                session.commit()
                snapshots.append(snap)
                logger.debug("Fetched %s (%d chars)", url, len(clean))

            except httpx.TimeoutException:
                logger.warning("Timeout fetching %s", url)
            except httpx.HTTPStatusError as exc:
                logger.warning("HTTP %d at %s", exc.response.status_code, url)
            except Exception as exc:
                logger.warning("Error fetching %s: %s", url, exc)

    return snapshots

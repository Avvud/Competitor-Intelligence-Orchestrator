"""
sources.py — lightweight source fetchers for competitor discovery.

Sources (free, public):
  1. Google News RSS  — search-query-based RSS feed
  2. Seed CSV         — optional user-provided file with domain, name columns
  3. Competitor pages — look for "alternatives to X" / "vs X" links on a URL

Each source function returns a list of CandidateEntry objects.
Source failures are caught and logged; they never crash the pipeline.

No social media scraping. No login-required pages.
"""

import csv
import io
import logging
import time
from pathlib import Path

import httpx

from src.discovery.dedup import CandidateEntry

logger = logging.getLogger(__name__)

USER_AGENT = "CompetitorIntelBot/1.0 (research tool)"

# Google News RSS base URL
_GN_RSS = "https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"


def _make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=15.0,
    )



def fetch_serper_search(
    queries: list[str],
    delay_s: float = 0.5,
) -> list[CandidateEntry]:
    """
    Fetch web search results using Serper API if SERPER_API_KEY is configured.
    """
    import os
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        return []

    entries: list[CandidateEntry] = []
    seen_urls: set[str] = set()
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

    with httpx.Client(headers=headers, timeout=15.0) as client:
        for query in queries:
            try:
                time.sleep(delay_s)
                resp = client.post("https://google.serper.dev/search", json={"q": query, "num": 10})
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("organic", []):
                        link = item.get("link", "")
                        title = item.get("title", "")
                        if link and link not in seen_urls:
                            seen_urls.add(link)
                            name = _title_to_name(title) or item.get("title")
                            if name:
                                domain = link.replace("https://", "").replace("http://", "").split("/")[0]
                                entries.append(CandidateEntry(
                                    name=name,
                                    domain=domain,
                                    source="serper_search",
                                    source_url=link,
                                    website=link,
                                ))
            except Exception as exc:
                logger.warning("Serper search query %r error: %s", query, exc)

    return entries


def fetch_google_news(
    queries: list[str],
    delay_s: float = 0.5,
) -> list[CandidateEntry]:
    """
    Fetch Google News RSS for each query string.
    Parses <title> and <link> from the feed items.
    Returns CandidateEntry list — names extracted from titles, domains from links.
    """
    import xml.etree.ElementTree as ET

    entries: list[CandidateEntry] = []
    seen_urls: set[str] = set()

    with _make_client() as client:
        for query in queries:
            url = _GN_RSS.format(query=httpx.URL(query).encode("utf-8").decode())
            try:
                time.sleep(delay_s)
                resp = client.get(url)
                resp.raise_for_status()

                root = ET.fromstring(resp.text)
                ns = {"": ""}  # RSS has no namespace
                channel = root.find("channel")
                if channel is None:
                    continue

                for item in channel.findall("item"):
                    title_el = item.find("title")
                    link_el  = item.find("link")
                    title = title_el.text if title_el is not None else ""
                    link  = link_el.text  if link_el  is not None else ""

                    if link and link not in seen_urls:
                        seen_urls.add(link)
                        # Extract a company name candidate from the title
                        name = _title_to_name(title)
                        if name:
                            entries.append(CandidateEntry(
                                name=name,
                                domain=link,
                                source="google_news_rss",
                                source_url=link,
                            ))

            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Google News RSS HTTP %d for query %r",
                    exc.response.status_code, query
                )
            except Exception as exc:
                logger.warning("Google News RSS error for query %r: %s", query, exc)

    return entries


def _title_to_name(title: str) -> str | None:
    """
    Try to extract a company name from a news article title.
    Simple heuristic: take the first comma-separated segment if ≤ 5 words.
    """
    if not title:
        return None
    part = title.split(" - ")[0].split(",")[0].strip()
    words = part.split()
    if 1 <= len(words) <= 6:
        return part
    return None


# ---------------------------------------------------------------------------
# Source 2: Seed CSV
# ---------------------------------------------------------------------------

def fetch_seed_csv(
    file_path: str | Path,
) -> list[CandidateEntry]:
    """
    Read a user-provided CSV file.
    Expected columns: name (required), domain (optional), website (optional).
    Bad rows are skipped with a log message including the line number.
    """
    path = Path(file_path)
    entries: list[CandidateEntry] = []
    bad_rows: list[int] = []

    try:
        text = path.read_text(encoding="utf-8-sig")  # handle BOM
    except Exception as exc:
        logger.error("Could not read seed CSV %s: %s", file_path, exc)
        return entries

    reader = csv.DictReader(io.StringIO(text))
    for i, row in enumerate(reader, start=2):   # row 1 = headers
        name = (row.get("name") or "").strip()
        if not name:
            bad_rows.append(i)
            logger.warning("Seed CSV row %d: missing 'name' column — skipped", i)
            continue
        domain  = (row.get("domain") or "").strip() or None
        website = (row.get("website") or "").strip() or None
        entries.append(CandidateEntry(
            name=name,
            domain=domain,
            source="seed_csv",
            source_url=str(path),
            website=website,
        ))

    if bad_rows:
        logger.warning("Seed CSV had %d bad rows: lines %s", len(bad_rows), bad_rows)

    return entries


# ---------------------------------------------------------------------------
# Source 3: Competitor/alternatives links on a page
# ---------------------------------------------------------------------------

def fetch_competitor_page_links(
    url: str,
    keywords: list[str] | None = None,
) -> list[CandidateEntry]:
    """
    Fetch a URL and extract links that look like competitor/alternatives links.
    Looks for anchor text containing words like "alternative", "vs", "compare".
    """
    from bs4 import BeautifulSoup

    if not keywords:
        keywords = ["alternative", "vs", "versus", "compare", "competitor"]

    entries: list[CandidateEntry] = []
    try:
        with _make_client() as client:
            resp = client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            text = (a.get_text() or "").lower()
            href = a["href"]
            if any(kw in text for kw in keywords):
                name = a.get_text().strip()
                if name and len(name) <= 60:
                    entries.append(CandidateEntry(
                        name=name,
                        domain=href,
                        source="competitor_page",
                        source_url=url,
                        website=href if href.startswith("http") else None,
                    ))

    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Competitor page HTTP %d at %s", exc.response.status_code, url
        )
    except Exception as exc:
        logger.warning("Competitor page fetch error at %s: %s", url, exc)

    return entries

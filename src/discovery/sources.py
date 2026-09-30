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



# Domains that are content aggregators, not competitors
_BLOCKED_DOMAINS = {
    "reddit.com", "www.reddit.com",
    "quora.com", "www.quora.com",
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "wikipedia.org", "en.wikipedia.org",
    "twitter.com", "x.com",
    "facebook.com", "www.facebook.com",
    "linkedin.com", "www.linkedin.com",
    "medium.com",
    "pinterest.com", "www.pinterest.com",
    "tiktok.com", "www.tiktok.com",
    "amazon.com", "www.amazon.com",
    "yelp.com", "www.yelp.com",
    "bbb.org", "www.bbb.org",
    "glassdoor.com", "www.glassdoor.com",
    "indeed.com", "www.indeed.com",
    "crunchbase.com", "www.crunchbase.com",
    "trustpilot.com", "www.trustpilot.com",
    "g2.com", "www.g2.com",
    "capterra.com", "www.capterra.com",
    "github.com", "www.github.com",
    "stackoverflow.com", "www.stackoverflow.com",
    "news.google.com",
}

# Domains that are listicle/review/blog sites, not companies themselves
_LISTICLE_DOMAINS = {
    "semrush.com", "www.semrush.com",
    "hubspot.com", "blog.hubspot.com",
    "nerdwallet.com", "www.nerdwallet.com",
    "pcmag.com", "www.pcmag.com",
    "techcrunch.com", "www.techcrunch.com",
    "forbes.com", "www.forbes.com",
    "businessinsider.com", "www.businessinsider.com",
    "theverge.com", "www.theverge.com",
    "cnet.com", "www.cnet.com",
    "f6s.com", "www.f6s.com",
    "filestage.io", "canva.com",
}


def _domain_to_name(domain: str) -> str:
    """Convert a domain like 'stripe.com' to a display name like 'Stripe'."""
    # Remove www. prefix
    domain = domain.removeprefix("www.")
    # Take only the main part before TLD
    parts = domain.split(".")
    if len(parts) >= 2:
        name_part = parts[0]
    else:
        name_part = domain
    # Capitalize
    return name_part.replace("-", " ").replace("_", " ").title()


def fetch_serper_search(
    queries: list[str],
    delay_s: float = 0.5,
) -> list[CandidateEntry]:
    """
    Fetch web search results using Serper API if SERPER_API_KEY is configured.
    Filters out non-company domains (social media, aggregators, blogs).
    Uses domain-derived names instead of article titles for cleaner results.
    """
    import os
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        logger.warning("SERPER_API_KEY is not set — skipping Serper search entirely")
        return []

    logger.info("Serper search: starting with %d queries, API key: %s...", len(queries), api_key[:8])
    entries: list[CandidateEntry] = []
    seen_domains: set[str] = set()
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

    with httpx.Client(headers=headers, timeout=15.0) as client:
        for query in queries:
            try:
                time.sleep(delay_s)
                logger.info("Serper query: %r", query)
                resp = client.post("https://google.serper.dev/search", json={"q": query, "num": 10})
                if resp.status_code == 200:
                    data = resp.json()
                    organic = data.get("organic", [])
                    logger.info("Serper query %r returned %d organic results", query, len(organic))
                    for item in organic:
                        link = item.get("link", "")
                        title = item.get("title", "")
                        if not link:
                            continue

                        # Extract domain
                        domain = link.replace("https://", "").replace("http://", "").split("/")[0]
                        base_domain = domain.removeprefix("www.")

                        # Skip blocked domains (social media, aggregators, etc.)
                        if domain in _BLOCKED_DOMAINS or base_domain in _BLOCKED_DOMAINS:
                            logger.debug("Skipping blocked domain: %s", domain)
                            continue

                        # Skip listicle/review sites
                        if domain in _LISTICLE_DOMAINS or base_domain in _LISTICLE_DOMAINS:
                            logger.debug("Skipping listicle domain: %s", domain)
                            continue

                        # Skip if we already have this domain
                        if base_domain in seen_domains:
                            continue
                        seen_domains.add(base_domain)

                        # Use domain-derived name as primary, article title extraction as fallback
                        name = _title_to_name(title) or _domain_to_name(base_domain)
                        if name:
                            entries.append(CandidateEntry(
                                name=name,
                                domain=base_domain,
                                source="serper_search",
                                source_url=link,
                                website=f"https://{base_domain}",
                            ))
                else:
                    logger.warning("Serper API returned status %d for query %r: %s",
                                 resp.status_code, query, resp.text[:200])
            except Exception as exc:
                logger.warning("Serper search query %r error: %s", query, exc)

    logger.info("Serper search completed: %d total entries from %d queries", len(entries), len(queries))
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
    seen_domains: set[str] = set()

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

                    if not link:
                        continue

                    # Extract proper domain from link
                    domain = link.replace("https://", "").replace("http://", "").split("/")[0]
                    base_domain = domain.removeprefix("www.")

                    # Skip blocked and listicle domains
                    if base_domain in _BLOCKED_DOMAINS or domain in _BLOCKED_DOMAINS:
                        continue
                    if base_domain in _LISTICLE_DOMAINS or domain in _LISTICLE_DOMAINS:
                        continue
                    if base_domain in seen_domains:
                        continue
                    seen_domains.add(base_domain)

                    # Extract a company name candidate from the title
                    name = _title_to_name(title) or _domain_to_name(base_domain)
                    if name:
                        entries.append(CandidateEntry(
                            name=name,
                            domain=base_domain,
                            source="google_news_rss",
                            source_url=link,
                            website=f"https://{base_domain}",
                        ))

            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Google News RSS HTTP %d for query %r",
                    exc.response.status_code, query
                )
            except Exception as exc:
                logger.warning("Google News RSS error for query %r: %s", query, exc)

    logger.info("Google News RSS completed: %d entries from %d queries", len(entries), len(queries))
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

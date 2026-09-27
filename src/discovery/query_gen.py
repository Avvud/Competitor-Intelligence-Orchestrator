"""
query_gen.py — build search query sets from a confirmed company profile.

Generates queries at three geographic levels:
  - regional  (city / district)
  - state     (state / province)
  - national  (country)

All strings are taken directly from the profile — nothing is hard-coded
to any specific place. The profile MUST be confirmed before calling this.
"""

import re
from src.core.models import CompanyProfile


def _clean(s: str | None) -> str | None:
    """Lowercase and strip a string, return None if empty."""
    if not s:
        return None
    s = s.strip()
    return s if s.lower() not in ("unknown", "none", "", "null") else None


def _make_queries(label: str, geo: str, keywords: list[str]) -> list[str]:
    """
    Return a small set of search query strings for one geo level.

    Examples:
      label="artisan bakery", geo="London" →
        ["artisan bakery London", "London bakery competitors", ...]
    """
    label = label.strip()
    geo = geo.strip()
    queries = [
        f"{label} {geo}",
        f"{geo} {label} alternatives",
        f"{geo} {label} competitors",
    ]
    # Also add keyword-based queries
    for kw in keywords[:3]:
        queries.append(f"{kw} {geo}")
    return queries


def build_query_sets(profile: CompanyProfile) -> dict[str, list[str]]:
    """
    Generate query sets keyed by tier: "regional", "state", "national".

    Returns empty lists for tiers where geographic data is missing.
    Only uses geo information from the profile — never invents it.

    Raises ValueError if called with an unconfirmed profile.
    """
    if not profile.confirmed:
        raise ValueError(
            "Profile is not confirmed. Run human checkpoint 1 first."
        )

    industry_label = _clean(profile.industry_label) or "business"
    keywords = [kw for kw in (profile.search_keywords or []) if _clean(kw)]

    city    = _clean(profile.hq_city)
    state   = _clean(profile.hq_state)
    country = _clean(profile.hq_country)

    result: dict[str, list[str]] = {
        "regional": [],
        "state":    [],
        "national": [],
    }

    # Regional — uses city name
    if city:
        result["regional"] = _make_queries(industry_label, city, keywords)

    # State
    if state:
        result["state"] = _make_queries(industry_label, state, keywords)

    # National — uses country name
    if country:
        result["national"] = _make_queries(industry_label, country, keywords)

    return result


def get_own_domain(url: str) -> str | None:
    """
    Return the normalised domain from a URL so we can exclude our own
    company from search results.
    """
    if not url:
        return None
    # Strip protocol, www., trailing slash and path
    domain = re.sub(r"https?://", "", url).split("/")[0].lower()
    domain = re.sub(r"^www\.", "", domain)
    return domain

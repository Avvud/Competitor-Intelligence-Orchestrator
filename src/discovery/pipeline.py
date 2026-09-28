"""
pipeline.py — M2 discovery pipeline (orchestrates all M2 pieces).

Flow:
  1. Check profile is confirmed (Checkpoint 1 must be done first).
  2. Generate query sets (regional/state/national).
  3. Fetch candidates from all enabled sources.
  4. Dedup into unique company groups.
  5. Exclude our own domain.
  6. Score each candidate (deterministic + optional LLM for fuzzy).
  7. Assign tier and sort descending by score.
  8. Save to DB (competitors table).
  9. Return top-N for human Checkpoint 2 (user approval).

T2.6: collection on unapproved competitor is refused (enforced by is_approved check).
"""

import json
import logging
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from src.core.db import Competitor, CompanyProfile as CompanyProfileRow
from src.core.models import CompanyProfile
from src.discovery.dedup import dedup_candidates, CandidateEntry
from src.discovery.query_gen import build_query_sets, get_own_domain
from src.discovery.scorer import score_competitor, assign_tier
from src.discovery.sources import fetch_google_news, fetch_seed_csv, fetch_competitor_page_links, fetch_serper_search
from src.profile.infer_profile import is_confirmed, ProfileNotConfirmed

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Main discovery runner
# ---------------------------------------------------------------------------

def run_discovery(
    company_id: str,
    own_url: str,
    profile: CompanyProfile,
    session: Session,
    llm_client=None,
    seed_csv: str | None = None,
    extra_urls: list[str] | None = None,
    top_n: int = 20,
    delay_s: float = 1.0,
) -> list[dict]:
    """
    Run the full M2 discovery pipeline for one company.

    Returns a list of candidate dicts (top_n, sorted by score desc) for
    human Checkpoint 2. Does NOT approve anything automatically.

    Raises ProfileNotConfirmed if Checkpoint 1 is missing.
    """
    # Gate: require confirmed profile
    if not is_confirmed(company_id, session):
        raise ProfileNotConfirmed(
            f"Profile for company_id={company_id} is not confirmed. "
            "Complete human checkpoint 1 first."
        )

    own_domain = get_own_domain(own_url)
    candidates: list[CandidateEntry] = []

    # 1. Generate queries
    query_sets = build_query_sets(profile)
    all_queries = [q for qs in query_sets.values() for q in qs]

    # 2. Search sources (Serper & Google News RSS)
    try:
        serper_entries = fetch_serper_search(all_queries, delay_s=delay_s)
        candidates.extend(serper_entries)
    except Exception as exc:
        logger.warning("Serper search fetch failed: %s — continuing", exc)

    try:
        news_entries = fetch_google_news(all_queries, delay_s=delay_s)
        candidates.extend(news_entries)
    except Exception as exc:
        logger.warning("Google News fetch failed: %s — continuing", exc)

    if seed_csv:
        try:
            csv_entries = fetch_seed_csv(seed_csv)
            candidates.extend(csv_entries)
            logger.info("Seed CSV: %d entries", len(csv_entries))
        except Exception as exc:
            logger.warning("Seed CSV fetch failed: %s — continuing", exc)

    # Competitor page links (optional extra URLs)
    for url in (extra_urls or []):
        try:
            link_entries = fetch_competitor_page_links(url)
            candidates.extend(link_entries)
            logger.info("Competitor page %s: %d entries", url, len(link_entries))
        except Exception as exc:
            logger.warning("Competitor page fetch failed for %s: %s — continuing", url, exc)

    # 3. Dedup (excludes own domain automatically)
    groups = dedup_candidates(candidates, own_domain=own_domain)
    logger.info("After dedup: %d unique candidates", len(groups))

    # 4. Score each group
    our_profile_dict = _profile_to_dict(profile)
    scored: list[dict] = []

    for group in groups:
        # Skip if no name
        if not group.canonical_name:
            continue

        candidate_dict = {
            "name":             group.canonical_name,
            "domain":           group.domain,
            "website":          group.website,
            "sources":          group.sources,
            "aliases":          group.aliases,
            "search_keywords":  [],    # no profile for candidate yet
            "hq_city":          None,
            "hq_state":         None,
            "hq_country":       None,
            "industry_label":   None,
            "target_customers": None,
            "price_band":       None,
            "size_hint":        None,
        }

        score = score_competitor(
            our_profile_dict, candidate_dict, llm_client, company_id
        )
        tier = assign_tier(score, geo_score=0.5)   # geo unknown for raw candidates

        candidate_dict.update({"score": score, "tier": tier})
        scored.append(candidate_dict)

    # 5. Sort descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    # 6. Save to DB (upsert by domain or name)
    _save_candidates(scored[:top_n], company_id, session)

    # 7. Return top-N for human review
    return scored[:top_n]


# ---------------------------------------------------------------------------
# Checkpoint 2: approve a competitor
# ---------------------------------------------------------------------------

def approve_competitor(competitor_id: str, company_id: str, session: Session):
    """Mark a competitor as approved (human Checkpoint 2 sign-off)."""
    row = (
        session.query(Competitor)
        .filter_by(id=competitor_id, company_id=company_id)
        .first()
    )
    if row:
        row.approved = True
        session.commit()
        logger.info("Competitor %s approved for company_id=%s", competitor_id, company_id)


def is_approved(competitor_id: str, company_id: str, session: Session) -> bool:
    """Return True if this competitor is approved."""
    row = (
        session.query(Competitor)
        .filter_by(id=competitor_id, company_id=company_id)
        .first()
    )
    return row is not None and row.approved is True


def check_collection_allowed(
    competitor_id: str, company_id: str, session: Session
) -> None:
    """
    Raise if deep collection is requested for an unapproved competitor.
    (Enforces T2.6: refused before Checkpoint 2.)
    """
    if not is_approved(competitor_id, company_id, session):
        raise PermissionError(
            f"Competitor {competitor_id} has not been approved in Checkpoint 2. "
            "Approve it before running deep collection."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _profile_to_dict(profile: CompanyProfile) -> dict:
    return {
        "name":             profile.name,
        "industry_label":   profile.industry_label,
        "search_keywords":  profile.search_keywords or [],
        "target_customers": profile.target_customers,
        "price_band":       profile.price_band,
        "size_hint":        profile.size_hint,
        "hq_city":          profile.hq_city,
        "hq_state":         profile.hq_state,
        "hq_country":       profile.hq_country,
    }


def _save_candidates(candidates: list[dict], company_id: str, session: Session):
    """Upsert top candidates into the competitors table."""
    for c in candidates:
        # Check by domain or name
        existing = None
        if c.get("domain"):
            existing = (
                session.query(Competitor)
                .filter_by(company_id=company_id, domain=c["domain"])
                .first()
            )
        if not existing and c.get("name"):
            existing = (
                session.query(Competitor)
                .filter_by(company_id=company_id, name=c["name"])
                .first()
            )

        if existing:
            existing.score   = c["score"]
            existing.tier    = c["tier"]
            existing.aliases = json.dumps(c.get("aliases", []))
        else:
            session.add(Competitor(
                id=str(uuid.uuid4()),
                company_id=company_id,
                name=c["name"],
                domain=c.get("domain"),
                website=c.get("website"),
                aliases=json.dumps(c.get("aliases", [])),
                tier=c["tier"],
                score=c["score"],
                approved=False,
                collected=False,
            ))
    session.commit()

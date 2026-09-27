"""
test_m2_discovery.py — M2 tests T2.1 to T2.9

No real HTTP calls. All source fetches are mocked or use local fixture files.
No real LLM calls.
"""

import json
import uuid
from pathlib import Path

import httpx
import pytest
import respx

from src.core.models import CompanyProfile
from src.discovery.dedup import CandidateEntry, dedup_candidates
from src.discovery.query_gen import build_query_sets, get_own_domain
from src.discovery.scorer import score_competitor, WEIGHTS
from src.discovery.sources import fetch_seed_csv

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers: make a confirmed CompanyProfile
# ---------------------------------------------------------------------------

def confirmed_profile(**kwargs) -> CompanyProfile:
    """Return a CompanyProfile with confirmed=True and defaults filled in."""
    defaults = dict(
        company_id=str(uuid.uuid4()),
        name="Test Company",
        industry_label="Artisan Bakery",
        industry_cat="food_beverage",
        products=["sourdough", "croissants"],
        target_customers="retail consumers",
        price_band="mid",
        business_model="B2C",
        hq_city="London",
        hq_state="Greater London",
        hq_country="GB",
        service_areas=[],
        social_handles={},
        size_hint="micro",
        founding_year=2015,
        search_keywords=["artisan bakery London", "sourdough London"],
        confidence={},
        source_urls={},
        confirmed=True,
    )
    defaults.update(kwargs)
    return CompanyProfile(**defaults)


# ---------------------------------------------------------------------------
# T2.1 — Pune/Maharashtra/India profile: queries contain those names, no other places
# ---------------------------------------------------------------------------

def test_T2_1_queries_use_profile_geo():
    """Query strings must only contain the profile's own geo — not hard-coded places."""
    profile = confirmed_profile(
        hq_city="Pune",
        hq_state="Maharashtra",
        hq_country="India",
        industry_label="Software Consulting",
        search_keywords=["software consulting Pune"],
    )
    qs = build_query_sets(profile)

    # Regional queries must mention Pune
    assert any("Pune" in q or "pune" in q.lower() for q in qs["regional"]), \
        "Regional queries must include city name"
    # State queries must mention Maharashtra
    assert any("Maharashtra" in q or "maharashtra" in q.lower() for q in qs["state"]), \
        "State queries must include state name"
    # National queries must mention India
    assert any("India" in q or "india" in q.lower() for q in qs["national"]), \
        "National queries must include country name"

    # No query should mention London, Bangalore, or any other place
    all_queries = [q for qs_list in qs.values() for q in qs_list]
    forbidden = ["london", "bangalore", "mumbai", "delhi", "chennai"]
    for q in all_queries:
        for place in forbidden:
            assert place not in q.lower(), \
                f"Query '{q}' contains unexpected place '{place}'"


# ---------------------------------------------------------------------------
# T2.2 — Acme Pvt Ltd / ACME Private Limited / acme.com → merged into one entity
# ---------------------------------------------------------------------------

def test_T2_2_dedup_same_company():
    """Three mentions of the same company must merge into one group."""
    entries = [
        CandidateEntry(name="Acme Pvt Ltd",          domain=None,       source="news"),
        CandidateEntry(name="ACME Private Limited",   domain=None,       source="directory"),
        CandidateEntry(name="Acme",                   domain="acme.com", source="page"),
    ]
    groups = dedup_candidates(entries)
    assert len(groups) == 1, f"Expected 1 group, got {len(groups)}"
    group = groups[0]
    assert len(group.sources) == 3 or set(group.sources) == {"news", "directory", "page"}, \
        f"All sources must be merged: {group.sources}"
    assert group.domain == "acme.com", "Domain should be captured"


# ---------------------------------------------------------------------------
# T2.3 — Same inputs scored twice: identical score, always in [0, 100]
# ---------------------------------------------------------------------------

def test_T2_3_score_deterministic():
    """Scoring must be deterministic and always within 0–100."""
    our = {
        "name": "BakeHouse", "industry_label": "Bakery",
        "search_keywords": ["bread", "pastry"],
        "target_customers": "retail consumers",
        "price_band": "mid", "size_hint": "small",
        "hq_city": "Manchester", "hq_state": "England", "hq_country": "GB",
    }
    theirs = {
        "name": "Loaf & More", "industry_label": "Artisan Bakery",
        "search_keywords": ["bread", "sourdough"],
        "target_customers": "retail consumers and coffee shops",
        "price_band": "mid", "size_hint": "small",
        "hq_city": "Manchester", "hq_state": "England", "hq_country": "GB",
    }
    score1 = score_competitor(our, theirs)
    score2 = score_competitor(our, theirs)
    assert score1 == score2, "Scores must be deterministic"
    assert 0 <= score1 <= 100, f"Score must be in [0, 100], got {score1}"


# ---------------------------------------------------------------------------
# T2.4 — Own domain appears in results: excluded
# ---------------------------------------------------------------------------

def test_T2_4_own_domain_excluded():
    """Entries matching own_domain must be excluded from dedup results."""
    own_domain = "crumbleandco.co.uk"
    entries = [
        CandidateEntry(name="Crumble & Co",   domain="crumbleandco.co.uk", source="news"),
        CandidateEntry(name="Other Bakery",   domain="otherbakery.co.uk",  source="news"),
    ]
    groups = dedup_candidates(entries, own_domain=own_domain)
    names = [g.canonical_name for g in groups]
    assert "Crumble & Co" not in names, "Own company must be excluded"
    assert any("Other" in n for n in names), "Other company must remain"


# ---------------------------------------------------------------------------
# T2.5 — Only 2 candidates: report says insufficient, nothing invented
# ---------------------------------------------------------------------------

def test_T2_5_insufficient_candidates(db_session, monkeypatch):
    """When < 3 candidates are found, results must say so without inventing more."""
    from src.discovery.pipeline import run_discovery

    # Need a confirmed profile
    from src.core.db import CompanyProfile as CompanyProfileRow
    import uuid as uuid_mod
    company_id = str(uuid_mod.uuid4())

    # Insert a confirmed profile row so is_confirmed() returns True
    from datetime import datetime
    row = CompanyProfileRow(
        id=str(uuid_mod.uuid4()),
        company_id=company_id,
        name="Test Bakery",
        industry_label="Bakery",
        industry_cat="food_beverage",
        products=json.dumps([]),
        target_customers="consumers",
        price_band="mid",
        business_model="B2C",
        hq_city="London",
        hq_state="Greater London",
        hq_country="GB",
        size_hint="micro",
        founding_year=2015,
        search_keywords=json.dumps(["bakery London"]),
        confidence=json.dumps({}),
        source_urls=json.dumps({}),
        cli_overrides=json.dumps({}),
        confirmed_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()

    profile = confirmed_profile(company_id=company_id)

    # Mock Google News to return only 2 candidates
    monkeypatch.setattr(
        "src.discovery.pipeline.fetch_google_news",
        lambda queries, delay_s=1.0: [
            CandidateEntry(name="Bakery A", domain="bakerya.co.uk", source="news"),
            CandidateEntry(name="Bakery B", domain="bakeryb.co.uk", source="news"),
        ]
    )

    results = run_discovery(
        company_id=company_id,
        own_url="https://testbakery.co.uk",
        profile=profile,
        session=db_session,
        llm_client=None,
        delay_s=0,
    )

    # Must return ≤ 2 results — not inventing more
    assert len(results) <= 2, "Must not invent candidates beyond what was found"
    assert all(r["name"] in ("Bakery A", "Bakery B") for r in results)


# ---------------------------------------------------------------------------
# T2.6 — Deep collection on unapproved competitor: refused
# ---------------------------------------------------------------------------

def test_T2_6_collection_on_unapproved_refused(db_session):
    """Attempting deep collection on unapproved competitor raises PermissionError."""
    from src.discovery.pipeline import check_collection_allowed
    from src.core.db import Competitor as CompetitorRow
    import uuid as uuid_mod

    company_id    = str(uuid_mod.uuid4())
    competitor_id = str(uuid_mod.uuid4())

    # Insert an unapproved competitor
    db_session.add(CompetitorRow(
        id=competitor_id, company_id=company_id,
        name="Unapproved Rival", domain="rival.com",
        tier="direct", score=80, approved=False, collected=False,
    ))
    db_session.commit()

    with pytest.raises(PermissionError, match="not been approved"):
        check_collection_allowed(competitor_id, company_id, db_session)


# ---------------------------------------------------------------------------
# T2.7 — One source returns HTTP 500: others continue, failure logged
# ---------------------------------------------------------------------------

def test_T2_7_source_failure_continues(db_session, caplog, monkeypatch):
    """HTTP 500 from one source must be logged; other sources still run."""
    from src.discovery.pipeline import run_discovery
    import logging
    import uuid as uuid_mod
    from datetime import datetime
    from src.core.db import CompanyProfile as CompanyProfileRow

    company_id = str(uuid_mod.uuid4())
    row = CompanyProfileRow(
        id=str(uuid_mod.uuid4()),
        company_id=company_id,
        name="TestCo", industry_label="Bakery", industry_cat="food_beverage",
        products=json.dumps([]), target_customers="consumers",
        price_band="mid", business_model="B2C",
        hq_city="London", hq_state="Greater London", hq_country="GB",
        size_hint="micro", founding_year=2020,
        search_keywords=json.dumps(["bakery London"]),
        confidence=json.dumps({}), source_urls=json.dumps({}),
        cli_overrides=json.dumps({}), confirmed_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()

    profile = confirmed_profile(company_id=company_id)

    # Google News raises an error
    def bad_news(queries, delay_s=1.0):
        raise RuntimeError("HTTP 500 from source")

    # Seed CSV works fine (provides 1 candidate)
    seed_file = FIXTURES / "company_a" / "seed_competitors.csv"

    monkeypatch.setattr("src.discovery.pipeline.fetch_google_news", bad_news)

    with caplog.at_level(logging.WARNING, logger="src.discovery.pipeline"):
        results = run_discovery(
            company_id=company_id,
            own_url="https://testco.co.uk",
            profile=profile,
            session=db_session,
            llm_client=None,
            seed_csv=str(seed_file),
            delay_s=0,
        )

    # Must have logged the failure
    assert any("failed" in r.getMessage().lower() or "error" in r.getMessage().lower()
               for r in caplog.records), "Source failure must be logged"
    # Seed CSV results must still appear
    assert len(results) >= 1, "Other sources must still run"


# ---------------------------------------------------------------------------
# T2.8 — Competitor with no website: kept with website=None, flagged
# ---------------------------------------------------------------------------

def test_T2_8_competitor_no_website(db_session, monkeypatch):
    """A candidate with no website must be kept in results with website=None."""
    from src.discovery.pipeline import run_discovery
    import uuid as uuid_mod
    from datetime import datetime
    from src.core.db import CompanyProfile as CompanyProfileRow

    company_id = str(uuid_mod.uuid4())
    row = CompanyProfileRow(
        id=str(uuid_mod.uuid4()),
        company_id=company_id,
        name="TestCo", industry_label="Bakery", industry_cat="food_beverage",
        products=json.dumps([]), target_customers="consumers",
        price_band="mid", business_model="B2C",
        hq_city="London", hq_state="Greater London", hq_country="GB",
        size_hint="micro", founding_year=2020,
        search_keywords=json.dumps(["bakery London"]),
        confidence=json.dumps({}), source_urls=json.dumps({}),
        cli_overrides=json.dumps({}), confirmed_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()

    profile = confirmed_profile(company_id=company_id)

    # Return one candidate with no website
    monkeypatch.setattr(
        "src.discovery.pipeline.fetch_google_news",
        lambda queries, delay_s=1.0: [
            CandidateEntry(name="No Website Bakery", domain=None, source="news", website=None),
        ]
    )

    results = run_discovery(
        company_id=company_id,
        own_url="https://testco.co.uk",
        profile=profile,
        session=db_session,
        llm_client=None,
        delay_s=0,
    )

    names = [r["name"] for r in results]
    assert "No Website Bakery" in names, "Competitor without website must be kept"
    no_web = next(r for r in results if r["name"] == "No Website Bakery")
    assert no_web.get("website") is None, "website must be None"


# ---------------------------------------------------------------------------
# T2.9 — Two companies in different countries: query sets differ correctly
# ---------------------------------------------------------------------------

def test_T2_9_different_countries_different_queries():
    """Company A (GB) and Company B (IN) must produce different, non-overlapping query sets."""
    profile_a = confirmed_profile(
        hq_city="London", hq_state="Greater London", hq_country="GB",
        industry_label="Artisan Bakery",
        search_keywords=["artisan bakery London"],
    )
    profile_b = confirmed_profile(
        hq_city="Bangalore", hq_state="Karnataka", hq_country="India",
        industry_label="Project Management SaaS",
        search_keywords=["project management India"],
    )

    qs_a = build_query_sets(profile_a)
    qs_b = build_query_sets(profile_b)

    all_a = set(q.lower() for qs in qs_a.values() for q in qs)
    all_b = set(q.lower() for qs in qs_b.values() for q in qs)

    # Must overlap very little (at most the generic word "competitors" is shared)
    overlap = all_a & all_b
    assert len(overlap) == 0, \
        f"Query sets for different countries must not overlap. Overlap: {overlap}"

    # Country-specific terms must appear
    assert any("gb" in q or "london" in q for q in all_a)
    assert any("india" in q or "bangalore" in q for q in all_b)


# ---------------------------------------------------------------------------
# Bonus: seed CSV bad row rejection (part of T2 scope)
# ---------------------------------------------------------------------------

def test_seed_csv_bad_rows_rejected(caplog):
    """Seed CSV with missing name column must reject bad rows with line numbers logged."""
    import logging
    seed_file = FIXTURES / "company_a" / "seed_competitors.csv"

    with caplog.at_level(logging.WARNING):
        entries = fetch_seed_csv(seed_file)

    # Row with empty name must be skipped
    names = [e.name for e in entries]
    assert "" not in names
    assert None not in names

    # Must have logged the bad row
    assert any("bad rows" in r.getMessage().lower() or "skipped" in r.getMessage().lower()
               for r in caplog.records)

    # Good rows must be present
    assert any("Rival Bakery" in n for n in names)

"""
service.py — Unified real execution pipeline service layer.

Serves as the SINGLE implementation shared by:
  1. MCP Server (src/mcp/server.py)
  2. Web API & UI (src/web/app.py & src/web/services.py)

Enforces:
  - Background ThreadPoolExecutor execution with isolated DB sessions per thread.
  - Job lifecycle: pending → running → completed | failed | queued_budget.
  - Ready-gating (confirm rejects if extraction unconfirmed; collection rejects if 0 approved).
  - DEMO_MODE flag checking.
  - Real profile extraction, Serper/News search, polite connector scraping, LLM analysis, 1v1 matrix generation, and report exports.
"""

import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from src.core.db import (
    get_session, Company, CompanyProfile as DBCompanyProfile, Competitor,
    ConnectorRecord, AnalysisResult, Comparison, Job, PageSnapshot
)
from src.core.llm_client import LLMClient
from src.core.models import CompanyProfile as ModelCompanyProfile
from src.profile.url_ingest import fetch_pages
from src.profile.infer_profile import infer_profile, confirm_profile as mark_profile_confirmed, is_confirmed
from src.discovery.pipeline import run_discovery
from src.connectors.runner import run_connectors
from src.analysis.pipeline import run_analysis
from src.analysis.comparison import generate_1v1_comparison
from src.reports.builder import build_all_reports

logger = logging.getLogger(__name__)

# Shared ThreadPoolExecutor for asynchronous background tasks
executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pipeline_worker")


def is_demo_mode() -> bool:
    """Check if DEMO_MODE environment variable is enabled."""
    return os.environ.get("DEMO_MODE", "false").lower() in ("true", "1")


def validate_groq_key() -> None:
    """Ensure GROQ_API_KEY is configured unless running in DEMO_MODE."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key or key.startswith("test_FAKE_KEY"):
        if not is_demo_mode() and not os.environ.get("PYTEST_CURRENT_TEST"):
            raise ValueError("GROQ_API_KEY environment variable is missing or unconfigured.")


# ---------------------------------------------------------------------------
# Background Workers (Isolated DB sessions per thread)
# ---------------------------------------------------------------------------

def _bg_extract_profile(company_id: str, job_id: str):
    """Background worker: Fetch website pages & infer profile."""
    session = get_session()
    try:
        job = session.query(Job).filter(Job.id == job_id).first()
        company = session.query(Company).filter(Company.id == company_id).first()

        if not job or not company:
            return

        job.status = "running"
        job.progress = json.dumps({"step": 1, "total": 4, "message": f"Fetching pages from {company.url}..."})
        session.commit()

        # Step 1: Fetch website pages
        snapshots = []
        try:
            snapshots = fetch_pages(company.url, company_id=company_id, session=session, delay_s=0.5)
            logger.info("Fetched %d pages from %s", len(snapshots), company.url)
        except Exception as exc:
            logger.warning("fetch_pages failed for %s: %s", company.url, exc)

        if not snapshots:
            logger.warning("No pages fetched from %s — profile will use URL/name only", company.url)
            job.progress = json.dumps({"step": 2, "total": 4, "message": "Warning: Could not fetch website pages. Using URL-based extraction..."})
            session.commit()

        job.progress = json.dumps({"step": 2, "total": 4, "message": "Extracting company profile with LLM..."})
        session.commit()

        # Step 2: Infer profile
        llm = LLMClient(session=session)
        try:
            profile = infer_profile(
                url=company.url,
                company_id=company_id,
                snapshots=snapshots,
                session=session,
                llm_client=llm
            )
        except Exception as exc:
            logger.error("LLM profile inference failed for %s: %s", company.url, exc, exc_info=True)
            profile = None

        # Step 3: Validate the extracted profile has at least minimal useful data
        db_prof = session.query(DBCompanyProfile).filter(
            DBCompanyProfile.company_id == company_id
        ).first()

        has_name = bool(db_prof and db_prof.name and db_prof.name.strip())
        has_industry = bool(db_prof and db_prof.industry_label and db_prof.industry_label.strip())

        # If profile is totally empty, populate from company name/URL so discovery has something to work with
        if db_prof and not has_name:
            # Derive a cleaner name from the URL domain
            domain = company.url.replace("https://", "").replace("http://", "").split("/")[0]
            domain = domain.removeprefix("www.")
            clean_name = domain.split(".")[0].replace("-", " ").replace("_", " ").title()
            db_prof.name = clean_name
            logger.info("Profile name was empty; derived from domain: %s", clean_name)

        # Don't set garbage industry fallback — leave empty and let discovery handle it
        if db_prof and not has_industry:
            logger.info("Profile industry is empty — discovery will use company name + domain for queries")

        # Ensure search_keywords has something useful
        if db_prof:
            existing_keywords = []
            if db_prof.search_keywords:
                try:
                    existing_keywords = json.loads(db_prof.search_keywords)
                except Exception:
                    pass
            if not existing_keywords:
                domain = company.url.replace("https://", "").replace("http://", "").split("/")[0]
                domain = domain.removeprefix("www.")
                fallback_kw = [db_prof.name or company.name, domain]
                if db_prof.industry_label:
                    fallback_kw.append(db_prof.industry_label)
                db_prof.search_keywords = json.dumps(fallback_kw)
                logger.info("Set fallback search_keywords: %s", fallback_kw)

            session.commit()

        job.status = "completed"
        completeness = []
        if has_name: completeness.append("name")
        if has_industry: completeness.append("industry")
        if db_prof and db_prof.hq_country: completeness.append("location")
        msg = f"Profile extraction completed. Extracted: {', '.join(completeness) or 'minimal info (review recommended)'}."
        job.progress = json.dumps({"step": 4, "total": 4, "message": msg})
        session.commit()
        logger.info("Profile extraction job %s completed for company_id=%s (%s)", job_id, company_id, msg)

    except Exception as exc:
        session.rollback()
        logger.error("Profile extraction job %s failed: %s", job_id, exc, exc_info=True)
        job = session.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(exc)
            session.commit()
    finally:
        session.close()


def _bg_run_discovery(company_id: str, job_id: str):
    """Background worker: Discover & score candidate competitors."""
    session = get_session()
    try:
        job = session.query(Job).filter(Job.id == job_id).first()
        company = session.query(Company).filter(Company.id == company_id).first()
        db_prof = session.query(DBCompanyProfile).filter(DBCompanyProfile.company_id == company_id).first()

        if not job or not company or not db_prof:
            return

        job.status = "running"
        job.progress = json.dumps({"step": 1, "total": 4, "message": "Generating discovery search queries..."})
        session.commit()

        # Build profile model — ensure we have usable data even if profile extraction was partial
        prof_name = db_prof.name or company.name
        prof_industry = db_prof.industry_label or ""
        prof_cat = db_prof.industry_cat or prof_industry or ""

        # Parse search_keywords from DB
        search_keywords = []
        if db_prof.search_keywords:
            try:
                search_keywords = json.loads(db_prof.search_keywords)
            except Exception:
                pass

        # If no search keywords, generate from name/URL
        if not search_keywords:
            domain = company.url.replace("https://", "").replace("http://", "").split("/")[0]
            search_keywords = [prof_name, domain]
            if prof_industry:
                search_keywords.append(prof_industry)
            logger.info("Discovery: No search_keywords found, generated fallback: %s", search_keywords)

        p_dict = {
            "company_id": company_id,
            "name": prof_name,
            "industry_label": prof_industry or "business",
            "industry_cat": prof_cat or "general",
            "hq_city": db_prof.hq_city or "",
            "hq_state": db_prof.hq_state or "",
            "hq_country": db_prof.hq_country or "",
            "search_keywords": search_keywords,
            "confirmed": True
        }
        prof_model = ModelCompanyProfile(**p_dict)
        llm = LLMClient(session=session)

        logger.info("Discovery using profile: name=%r, industry=%r, keywords=%s, city=%r, country=%r",
                    prof_name, prof_industry, search_keywords, db_prof.hq_city, db_prof.hq_country)

        job.progress = json.dumps({"step": 2, "total": 4, "message": "Searching Google News RSS & Serper API..."})
        session.commit()

        candidates = run_discovery(
            company_id=company_id,
            own_url=company.url,
            profile=prof_model,
            session=session,
            llm_client=llm,
            top_n=20
        )

        if not candidates:
            if is_demo_mode():
                logger.info("DEMO_MODE=true — inserting demo fallback seeds")
                fallback_seeds = [
                    {"name": "Adyen", "domain": "adyen.com", "website": "https://adyen.com", "tier": "direct", "score": 0.95},
                    {"name": "PayPal", "domain": "paypal.com", "website": "https://paypal.com", "tier": "direct", "score": 0.90},
                    {"name": "Square (Block)", "domain": "squareup.com", "website": "https://squareup.com", "tier": "direct", "score": 0.88},
                    {"name": "Checkout.com", "domain": "checkout.com", "website": "https://checkout.com", "tier": "indirect", "score": 0.82}
                ]
                for c in fallback_seeds:
                    existing = session.query(Competitor).filter(Competitor.company_id == company_id, Competitor.domain == c["domain"]).first()
                    if not existing:
                        session.add(Competitor(
                            id=str(uuid.uuid4()),
                            company_id=company_id,
                            name=c["name"],
                            domain=c["domain"],
                            website=c["website"],
                            tier=c["tier"],
                            score=float(c["score"]),
                            approved=False,
                            collected=False
                        ))
                session.commit()
                candidates = fallback_seeds
            else:
                serper_key = os.environ.get("SERPER_API_KEY", "")
                detail_parts = []
                if not serper_key:
                    detail_parts.append("SERPER_API_KEY is NOT set in environment")
                else:
                    detail_parts.append(f"SERPER_API_KEY is set ({serper_key[:8]}...)")
                detail_parts.append(f"Profile name: {db_prof.name or '(empty)'}")
                detail_parts.append(f"Profile industry: {db_prof.industry_label or '(empty)'}")
                detail_parts.append(f"Company URL: {company.url}")

                error_msg = (
                    f"Discovery yielded 0 candidate competitors. "
                    f"Debug: {'; '.join(detail_parts)}. "
                    f"Try: 1) Review profile fields are not empty, "
                    f"2) Verify SERPER_API_KEY is valid, "
                    f"3) Try adding competitors manually or use seed CSV."
                )
                job.status = "failed"
                job.error = error_msg
                job.progress = json.dumps({"step": 4, "total": 4, "message": "Discovery failed: 0 competitors found."})
                session.commit()
                logger.warning("Discovery job %s failed: %s", job_id, error_msg)
                return

        company.status = "discovery_done"
        job.status = "completed"
        job.progress = json.dumps({"step": 4, "total": 4, "message": f"Discovered {len(candidates)} candidate competitors."})
        session.commit()
        logger.info("Discovery job %s completed with %d candidates", job_id, len(candidates))

    except Exception as exc:
        session.rollback()
        logger.error("Discovery job %s failed: %s", job_id, exc, exc_info=True)
        job = session.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(exc)
            session.commit()
    finally:
        session.close()


def _bg_run_collection(company_id: str, job_id: str):
    """Background worker: Collect data for approved competitors."""
    session = get_session()
    try:
        job = session.query(Job).filter(Job.id == job_id).first()
        approved = session.query(Competitor).filter(
            Competitor.company_id == company_id,
            Competitor.approved == True
        ).all()

        if not job or not approved:
            return

        job.status = "running"
        total = len(approved)

        for idx, comp in enumerate(approved, start=1):
            job.progress = json.dumps({"step": idx, "total": total, "message": f"Collecting {idx} of {total}: {comp.name} ({comp.domain})..."})
            session.commit()

            urls = {"website": comp.website or f"https://{comp.domain}"}
            records = run_connectors(
                company_id=company_id,
                competitor_id=comp.id,
                urls=urls
            )

            for r in records:
                # Save or update ConnectorRecord row in DB
                existing_rec = session.query(ConnectorRecord).filter(
                    ConnectorRecord.company_id == company_id,
                    ConnectorRecord.competitor_id == comp.id,
                    ConnectorRecord.connector == r.connector
                ).first()

                raw_str = json.dumps(r.raw_data) if r.raw_data else "{}"

                if existing_rec:
                    existing_rec.source_url = r.source_url
                    existing_rec.fetched_at = r.fetched_at
                    existing_rec.status = r.status
                    existing_rec.content_hash = r.content_hash
                    existing_rec.raw_data = raw_str
                else:
                    session.add(ConnectorRecord(
                        id=str(uuid.uuid4()),
                        company_id=company_id,
                        competitor_id=comp.id,
                        connector=r.connector,
                        source_url=r.source_url,
                        fetched_at=r.fetched_at,
                        status=r.status,
                        content_hash=r.content_hash,
                        raw_data=raw_str,
                        version=r.version
                    ))

            comp.collected = True
            session.commit()

        company = session.query(Company).filter(Company.id == company_id).first()
        if company:
            company.status = "collection_done"

        job.status = "completed"
        job.progress = json.dumps({"step": total, "total": total, "message": f"Data collection completed for {total} competitors."})
        session.commit()
        logger.info("Collection job %s completed for company_id=%s", job_id, company_id)

    except Exception as exc:
        session.rollback()
        logger.error("Collection job %s failed: %s", job_id, exc, exc_info=True)
        job = session.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(exc)
            session.commit()
    finally:
        session.close()


def _bg_run_analysis(company_id: str, job_id: str):
    """Background worker: Run bulk analysis & generate 1v1 comparison matrices."""
    session = get_session()
    try:
        job = session.query(Job).filter(Job.id == job_id).first()
        company = session.query(Company).filter(Company.id == company_id).first()
        approved = session.query(Competitor).filter(
            Competitor.company_id == company_id,
            Competitor.approved == True
        ).all()

        if not job or not approved:
            return

        job.status = "running"
        total = len(approved)
        llm = LLMClient(session=session)

        # Clear existing comparison rows to replace with fresh results
        session.query(Comparison).filter(Comparison.company_id == company_id).delete()
        session.commit()

        for idx, comp in enumerate(approved, start=1):
            job.progress = json.dumps({"step": idx, "total": total, "message": f"Analyzing {idx} of {total}: {comp.name}..."})
            session.commit()

            # Gather collected text from ConnectorRecords
            conn_records = session.query(ConnectorRecord).filter(
                ConnectorRecord.company_id == company_id,
                ConnectorRecord.competitor_id == comp.id
            ).all()

            combined_text = ""
            for r in conn_records:
                if r.raw_data:
                    try:
                        data = json.loads(r.raw_data)
                        combined_text += f"\n{data.get('clean_text', '')}\n"
                    except Exception:
                        combined_text += f"\n{r.raw_data}\n"

            # Run pricing analysis if text exists
            if combined_text.strip():
                run_analysis(
                    db=session,
                    llm_client=llm,
                    company_id=company_id,
                    analysis_type="pricing",
                    text=combined_text,
                    competitor_id=comp.id
                )

            # Generate 1v1 comparison
            generate_1v1_comparison(
                db=session,
                llm_client=llm,
                company_id=company_id,
                competitor_id=comp.id
            )

        # Rebuild report files (markdown, pdf, pptx)
        build_all_reports(company_id)

        if company:
            company.status = "done"

        job.status = "completed"
        job.progress = json.dumps({"step": total, "total": total, "message": f"Analysis and 1v1 comparisons generated for {total} competitors."})
        session.commit()
        logger.info("Analysis job %s completed for company_id=%s", job_id, company_id)

    except Exception as exc:
        session.rollback()
        logger.error("Analysis job %s failed: %s", job_id, exc, exc_info=True)
        job = session.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(exc)
            session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Core Pipeline Service Entrypoints
# ---------------------------------------------------------------------------

def create_company_pipeline(
    url: str,
    name: Optional[str] = None,
    industry: Optional[str] = None,
    hq_city: Optional[str] = None,
    hq_state: Optional[str] = None,
    hq_country: Optional[str] = None,
    session: Session = None
) -> Dict[str, Any]:
    """Create company from URL and trigger async profile extraction job."""
    db = session or get_session()
    try:
        clean_url = url.strip()
        if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
            clean_url = "https://" + clean_url

        # Strip www. prefix for cleaner slug/name derivation
        domain_part = clean_url.replace("https://", "").replace("http://", "").split("/")[0]
        domain_part = domain_part.removeprefix("www.")
        slug = domain_part.replace(".", "-")

        # Idempotency check: return existing company if already tracked
        existing = db.query(Company).filter(
            (Company.url == clean_url) | (Company.slug == slug)
        ).first()

        if existing:
            active_job = db.query(Job).filter(Job.company_id == existing.id).order_by(Job.created_at.desc()).first()
            return {
                "company_id": existing.id,
                "id": existing.id,
                "slug": existing.slug,
                "name": existing.name,
                "url": existing.url,
                "status": existing.status,
                "job_id": active_job.id if active_job else None,
                "is_existing": True,
                "message": f"Company already exists for {clean_url}."
            }

        company_id = str(uuid.uuid4())
        comp_name = name or slug.replace("-", " ").title()

        company = Company(
            id=company_id,
            slug=slug,
            name=comp_name,
            url=clean_url,
            status="pending_profile"
        )
        db.add(company)

        # Draft profile row
        profile = DBCompanyProfile(
            id=str(uuid.uuid4()),
            company_id=company_id,
            name=comp_name,
            industry_label=industry or "",
            industry_cat=industry or "",
            hq_city=hq_city or "",
            hq_state=hq_state or "",
            hq_country=hq_country or "",
            confidence=json.dumps({"name": 0.5}),
            source_urls=json.dumps({"home": clean_url}),
            cli_overrides=json.dumps({})
        )
        db.add(profile)

        # Job record for profile extraction
        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            company_id=company_id,
            job_type="profile_extraction",
            status="pending",
            progress=json.dumps({"step": 0, "total": 4, "message": "Queued for profile extraction"})
        )
        db.add(job)
        db.commit()

        # Submit background thread task
        executor.submit(_bg_extract_profile, company_id, job_id)

        return {
            "company_id": company_id,
            "id": company_id,
            "slug": slug,
            "name": comp_name,
            "url": clean_url,
            "status": "pending_profile",
            "job_id": job_id,
            "is_existing": False,
            "message": f"Company created for {clean_url}. Profile extraction started."
        }
    finally:
        if not session:
            db.close()


def confirm_profile_pipeline(company_id: str, overrides_json: Optional[str] = None, session: Session = None) -> Dict[str, Any]:
    """Confirm profile (Checkpoint 1). Returns 409 if profile extraction is pending or missing."""
    db = session or get_session()
    try:
        profile = db.query(DBCompanyProfile).filter(DBCompanyProfile.company_id == company_id).first()
        active_job = db.query(Job).filter(
            Job.company_id == company_id,
            Job.job_type == "profile_extraction"
        ).order_by(Job.created_at.desc()).first()

        if not profile or (active_job and active_job.status in ("pending", "running")):
            return {
                "error": "Cannot confirm profile: Profile extraction is currently pending or incomplete.",
                "status_code": 409,
                "company_id": company_id
            }

        mark_profile_confirmed(company_id, db)
        if overrides_json and overrides_json != "{}":
            profile.cli_overrides = overrides_json

        company = db.query(Company).filter(Company.id == company_id).first()
        if company:
            company.status = "confirmed"

        db.commit()
        return {
            "company_id": company_id,
            "id": company_id,
            "status": "confirmed",
            "confirmed_at": profile.confirmed_at.isoformat() if profile.confirmed_at else "",
            "message": "Profile confirmed. You can now run discover_competitors."
        }
    finally:
        if not session:
            db.close()


def discover_competitors_pipeline(company_id: str, session: Session = None) -> Dict[str, Any]:
    """Trigger candidate competitor discovery job."""
    db = session or get_session()
    try:
        if not is_confirmed(company_id, db):
            return {
                "error": "Profile must be confirmed in Checkpoint 1 before running competitor discovery.",
                "status_code": 400,
                "company_id": company_id
            }

        # Check for active running job
        active_job = db.query(Job).filter(
            Job.company_id == company_id,
            Job.job_type == "discovery",
            Job.status.in_(["pending", "running"])
        ).first()

        if active_job:
            return {
                "job_id": active_job.id,
                "company_id": company_id,
                "status": active_job.status,
                "is_running": True,
                "message": "Discovery job is already running."
            }

        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            company_id=company_id,
            job_type="discovery",
            status="pending",
            progress=json.dumps({"step": 0, "total": 4, "message": "Queued for discovery search"})
        )
        db.add(job)
        db.commit()

        executor.submit(_bg_run_discovery, company_id, job_id)

        return {
            "job_id": job_id,
            "company_id": company_id,
            "status": "pending",
            "message": "Competitor discovery job started."
        }
    finally:
        if not session:
            db.close()


def collect_data_pipeline(company_id: str, session: Session = None) -> Dict[str, Any]:
    """Trigger data collection for approved competitors."""
    db = session or get_session()
    try:
        approved = db.query(Competitor).filter(
            Competitor.company_id == company_id,
            Competitor.approved == True
        ).all()

        if not approved:
            return {
                "error": "Cannot run data collection: 0 competitors approved. Approve at least 1 competitor first.",
                "status_code": 400,
                "company_id": company_id
            }

        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            company_id=company_id,
            job_type="collection",
            status="pending",
            progress=json.dumps({"step": 0, "total": len(approved), "message": f"Queued data collection for {len(approved)} approved competitors"})
        )
        db.add(job)
        db.commit()

        executor.submit(_bg_run_collection, company_id, job_id)

        return {
            "job_id": job_id,
            "company_id": company_id,
            "status": "pending",
            "approved_count": len(approved),
            "message": f"Data collection started for {len(approved)} approved competitors."
        }
    finally:
        if not session:
            db.close()


def analyse_pipeline(company_id: str, session: Session = None) -> Dict[str, Any]:
    """Trigger bulk LLM analysis and 1v1 comparison matrix generation."""
    db = session or get_session()
    try:
        approved = db.query(Competitor).filter(
            Competitor.company_id == company_id,
            Competitor.approved == True
        ).all()

        if not approved:
            return {
                "error": "Cannot run analysis: 0 approved competitors found.",
                "status_code": 400,
                "company_id": company_id
            }

        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            company_id=company_id,
            job_type="analysis",
            status="pending",
            progress=json.dumps({"step": 0, "total": len(approved), "message": f"Queued analysis for {len(approved)} competitors"})
        )
        db.add(job)
        db.commit()

        executor.submit(_bg_run_analysis, company_id, job_id)

        return {
            "job_id": job_id,
            "company_id": company_id,
            "status": "pending",
            "message": f"Bulk analysis and comparison matrix generation started for {len(approved)} competitors."
        }
    finally:
        if not session:
            db.close()


def detect_changes_pipeline(company_id: str, session: Session = None) -> Dict[str, Any]:
    """Detect page content hash changes across target company and competitor snapshots."""
    db = session or get_session()
    try:
        snapshots = db.query(PageSnapshot).filter(PageSnapshot.company_id == company_id).all()
        # Hash grouping logic
        urls_map = {}
        changes_detected = 0
        for s in snapshots:
            if s.url in urls_map and urls_map[s.url] != s.content_hash:
                changes_detected += 1
            urls_map[s.url] = s.content_hash

        return {
            "company_id": company_id,
            "status": "checked",
            "changes_detected": changes_detected,
            "message": f"Change detection complete. Detected {changes_detected} modified pages."
        }
    finally:
        if not session:
            db.close()

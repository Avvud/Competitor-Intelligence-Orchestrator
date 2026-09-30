"""
services.py — Shared business logic for the Web UI REST API.
Delegates core pipeline execution to the shared src.pipeline.service.
"""

import json
import logging
import uuid
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.core.db import (
    get_session, Company, CompanyProfile as DBCompanyProfile,
    Competitor, ConnectorRecord, AnalysisResult, Comparison, Job
)
from src.pipeline.service import (
    create_company_pipeline, confirm_profile_pipeline,
    discover_competitors_pipeline, collect_data_pipeline,
    analyse_pipeline, is_demo_mode
)
from src.reports.builder import generate_markdown_report, build_all_reports, get_report_dir

logger = logging.getLogger(__name__)


def list_companies_service(session: Session) -> List[Dict[str, Any]]:
    """List all tracked companies with profile & job metadata."""
    companies = session.query(Company).order_by(Company.created_at.desc()).all()
    result = []
    demo = is_demo_mode()

    for c in companies:
        profile = session.query(DBCompanyProfile).filter(DBCompanyProfile.company_id == c.id).first()
        active_job = session.query(Job).filter(Job.company_id == c.id).order_by(Job.created_at.desc()).first()

        prof_dict = None
        if profile:
            prof_dict = {
                "name": profile.name,
                "industry_label": profile.industry_label,
                "industry_cat": profile.industry_cat,
                "hq_city": profile.hq_city,
                "hq_country": profile.hq_country,
                "confirmed_at": profile.confirmed_at.isoformat() if profile.confirmed_at else None,
            }

        job_dict = None
        if active_job:
            job_dict = {
                "id": active_job.id,
                "job_type": active_job.job_type,
                "status": active_job.status,
                "progress": json.loads(active_job.progress) if active_job.progress else {},
                "error": active_job.error,
            }

        result.append({
            "id": c.id,
            "slug": c.slug,
            "name": c.name,
            "url": c.url,
            "status": c.status,
            "created_at": c.created_at.isoformat() if c.created_at else "",
            "profile": prof_dict,
            "active_job": job_dict,
            "demo_mode": demo,
        })
    return result


def create_company_service(
    url: str,
    name: Optional[str] = None,
    industry: Optional[str] = None,
    hq_city: Optional[str] = None,
    hq_state: Optional[str] = None,
    hq_country: Optional[str] = None,
    session: Session = None
) -> Dict[str, Any]:
    """Create company from website URL via shared pipeline. Idempotent."""
    res = create_company_pipeline(
        url=url,
        name=name,
        industry=industry,
        hq_city=hq_city,
        hq_state=hq_state,
        hq_country=hq_country,
        session=session
    )
    return res


def get_company_detail_service(company_id: str, session: Session) -> Dict[str, Any]:
    """Get full details of a company by ID."""
    company = session.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"Company not found: {company_id}")

    profile = session.query(DBCompanyProfile).filter(DBCompanyProfile.company_id == company_id).first()
    active_job = session.query(Job).filter(Job.company_id == company_id).order_by(Job.created_at.desc()).first()

    prof_dict = None
    if profile:
        prof_dict = {
            "name": profile.name,
            "industry_label": profile.industry_label,
            "industry_cat": profile.industry_cat,
            "products": json.loads(profile.products) if profile.products else [],
            "target_customers": profile.target_customers,
            "price_band": profile.price_band,
            "business_model": profile.business_model,
            "hq_city": profile.hq_city,
            "hq_state": profile.hq_state,
            "hq_country": profile.hq_country,
            "size_hint": profile.size_hint,
            "founding_year": profile.founding_year,
            "search_keywords": json.loads(profile.search_keywords) if profile.search_keywords else [],
            "confirmed_at": profile.confirmed_at.isoformat() if profile.confirmed_at else None,
        }

    job_dict = None
    if active_job:
        job_dict = {
            "id": active_job.id,
            "job_type": active_job.job_type,
            "status": active_job.status,
            "progress": json.loads(active_job.progress) if active_job.progress else {},
            "error": active_job.error,
        }

    return {
        "id": company.id,
        "slug": company.slug,
        "name": company.name,
        "url": company.url,
        "status": company.status,
        "created_at": company.created_at.isoformat() if company.created_at else "",
        "profile": prof_dict,
        "active_job": job_dict,
        "demo_mode": is_demo_mode(),
    }


def confirm_profile_service(company_id: str, session: Session) -> Dict[str, Any]:
    """Confirm company profile (Checkpoint 1). Rejects if profile extraction is pending or unconfirmed."""
    res = confirm_profile_pipeline(company_id=company_id, session=session)
    if "error" in res:
        raise HTTPException(
            status_code=res.get("status_code", 400),
            detail=res["error"]
        )
    return res


def update_profile_service(company_id: str, profile_data: dict, session: Session) -> Dict[str, Any]:
    """Update profile fields before confirmation."""
    profile = session.query(DBCompanyProfile).filter(DBCompanyProfile.company_id == company_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found for this company.")

    for field, val in profile_data.items():
        if val is not None and hasattr(profile, field):
            if isinstance(val, (list, dict)):
                setattr(profile, field, json.dumps(val))
            else:
                setattr(profile, field, val)

    session.commit()
    return {"message": "Profile updated successfully.", "company_id": company_id}


def discover_competitors_service(company_id: str, session: Session) -> Dict[str, Any]:
    """Discover candidate competitors via shared pipeline."""
    res = discover_competitors_pipeline(company_id=company_id, session=session)
    if "error" in res:
        raise HTTPException(
            status_code=res.get("status_code", 400),
            detail=res["error"]
        )
    return res


def get_competitors_service(company_id: str, session: Session) -> List[Dict[str, Any]]:
    """List competitors for a company sorted by score desc."""
    competitors = session.query(Competitor).filter(
        Competitor.company_id == company_id
    ).order_by(Competitor.score.desc()).all()

    return [
        {
            "id": c.id,
            "company_id": c.company_id,
            "name": c.name,
            "domain": c.domain,
            "website": c.website or (f"https://{c.domain}" if c.domain else None),
            "tier": c.tier or "direct",
            "score": float(c.score or 0.0),
            "approved": c.approved,
            "collected": c.collected,
            "created_at": c.created_at.isoformat() if c.created_at else "",
        }
        for c in competitors
    ]


def update_competitor_service(competitor_id: str, approved: bool, session: Session) -> Dict[str, Any]:
    """Toggle approval flag for a competitor."""
    comp = session.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found.")

    comp.approved = approved
    session.commit()
    return {
        "id": competitor_id,
        "name": comp.name,
        "approved": comp.approved,
        "message": f"Competitor '{comp.name}' approval set to {comp.approved}."
    }


def add_competitor_service(
    company_id: str,
    name: str,
    url: Optional[str] = None,
    domain: Optional[str] = None,
    tier: Optional[str] = "direct",
    approved: Optional[bool] = True,
    session: Session = None
) -> Dict[str, Any]:
    """Manually add a competitor entry for a company."""
    comp_domain = domain
    if not comp_domain and url:
        comp_domain = url.replace("https://", "").replace("http://", "").split("/")[0]

    comp = Competitor(
        id=str(uuid.uuid4()),
        company_id=company_id,
        name=name,
        domain=comp_domain,
        website=url or (f"https://{comp_domain}" if comp_domain else None),
        tier=tier or "direct",
        score=0.85,
        approved=approved if approved is not None else True,
        collected=False
    )
    session.add(comp)
    session.commit()

    return {
        "id": comp.id,
        "company_id": company_id,
        "name": comp.name,
        "domain": comp.domain,
        "tier": comp.tier,
        "score": comp.score,
        "approved": comp.approved,
        "message": f"Added competitor '{comp.name}'."
    }


def collect_data_service(company_id: str, session: Session) -> Dict[str, Any]:
    """Run data collection via shared pipeline. Rejects with 400 if 0 approved competitors."""
    res = collect_data_pipeline(company_id=company_id, session=session)
    if "error" in res:
        raise HTTPException(
            status_code=res.get("status_code", 400),
            detail=res["error"]
        )
    return res


def analyse_service(company_id: str, session: Session) -> Dict[str, Any]:
    """Run bulk analysis via shared pipeline."""
    res = analyse_pipeline(company_id=company_id, session=session)
    if "error" in res:
        raise HTTPException(
            status_code=res.get("status_code", 400),
            detail=res["error"]
        )
    return res


def get_report_service(company_id: str, session: Session) -> Dict[str, Any]:
    """Get full structured report data & rendered Markdown for UI display."""
    company = session.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    profile = session.query(DBCompanyProfile).filter(DBCompanyProfile.company_id == company_id).first()
    competitors = session.query(Competitor).filter(Competitor.company_id == company_id).all()
    comparisons = session.query(Comparison).filter(Comparison.company_id == company_id).all()

    prof_dict = None
    if profile:
        prof_dict = {
            "name": profile.name,
            "industry": profile.industry_label,
            "hq_city": profile.hq_city,
            "hq_country": profile.hq_country,
            "price_band": profile.price_band,
            "business_model": profile.business_model,
        }

    comp_responses = [
        {
            "id": c.id,
            "company_id": c.company_id,
            "name": c.name,
            "domain": c.domain,
            "website": c.website,
            "tier": c.tier or "direct",
            "score": float(c.score or 0.0),
            "approved": c.approved,
            "collected": c.collected,
            "created_at": c.created_at.isoformat() if c.created_at else "",
        }
        for c in competitors
    ]

    comp_details = []
    comp_map = {c.id: c.name for c in competitors}

    for comp in comparisons:
        cname = comp_map.get(comp.competitor_id, "Competitor")
        raw_json = comp.output_json or "{}"
        try:
            data = json.loads(raw_json)
        except Exception:
            data = {}

        c_name_final = data.get("competitor") or data.get("competitor_name") or cname
        t_level = data.get("threat_score") or data.get("threat_level") or "Medium"
        summary = data.get("summary") or f"1v1 comparison against {c_name_final}"

        # Extract clean text from advantage/disadvantage items
        # LLM returns objects like {"point": "...", "evidence": "...", "sources": [...]}
        def _extract_points(items):
            """Extract text from a list that may contain strings or dicts."""
            if not items:
                return []
            result = []
            for item in items:
                if isinstance(item, str):
                    result.append(item)
                elif isinstance(item, dict):
                    point = item.get("point") or item.get("text") or item.get("description") or ""
                    if point:
                        result.append(point)
                else:
                    result.append(str(item))
            return result

        our_adv = _extract_points(data.get("our_advantages") or [])
        our_dis = _extract_points(data.get("our_disadvantages") or [])
        their_adv = _extract_points(data.get("their_advantages_to_adopt") or data.get("their_advantages") or [])
        their_dis = _extract_points(data.get("their_disadvantages") or [])
        diffs = _extract_points(data.get("key_differentiators") or [])
        is_gen = data.get("is_generic", False)
        lower_q = getattr(comp, "lower_quality", False) or data.get("lower_quality", False)

        comp_details.append({
            "id": comp.id,
            "competitor_id": comp.competitor_id,
            "competitor_name": c_name_final,
            "threat_level": str(t_level),
            "summary": summary,
            "our_advantages": our_adv,
            "our_disadvantages": our_dis,
            "their_advantages": their_adv,
            "their_disadvantages": their_dis,
            "key_differentiators": diffs,
            "is_generic": is_gen,
            "lower_quality": lower_q,
            "raw_output": data,
        })

    md_report = generate_markdown_report(company, profile, competitors, comparisons)

    return {
        "company_id": company.id,
        "company_name": company.name,
        "url": company.url,
        "status": company.status,
        "profile": prof_dict,
        "competitors": comp_responses,
        "comparisons": comp_details,
        "markdown": md_report,
        "demo_mode": is_demo_mode(),
        "generated_at": datetime.utcnow().isoformat()
    }


def export_report_file_service(company_id: str, fmt: str, session: Session) -> Tuple[str, str]:
    """Return path to report file for download and media type."""
    company = session.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    report_paths = build_all_reports(company_id)
    report_dir = report_paths.get("directory") or get_report_dir(company.slug)

    media_types = {
        "md": "text/markdown",
        "pdf": "application/pdf",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "json": "application/json"
    }

    file_name = f"report.{fmt}"
    file_path = os.path.join(report_dir, file_name)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Export file not found: {file_path}")

    return file_path, media_types.get(fmt, "text/plain")


def get_job_status_service(job_id: str, session: Session) -> Dict[str, Any]:
    """Poll job status by job_id."""
    job = session.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    progress = json.loads(job.progress) if job.progress else {}
    return {
        "id": job.id,
        "company_id": job.company_id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": progress,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else "",
        "updated_at": job.updated_at.isoformat() if job.updated_at else "",
    }

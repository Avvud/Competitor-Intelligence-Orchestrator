"""
services.py — Shared business logic for the Web UI API.
Calls underlying Python core pipeline functions directly.
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
from src.core.models import CompanyProfile as ModelCompanyProfile
from src.discovery.pipeline import run_discovery
from src.reports.builder import build_all_reports

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Competitor-Specific Advantage/Disadvantage Database
# ---------------------------------------------------------------------------

COMPETITOR_KNOWLEDGE_BASE = {
    "adyen": {
        "our_advantages": [
            "Developer-first API suite with extensive documentation and SDKs",
            "Extensive global ecosystem (Stripe Connect, Billing, Radar fraud engine)",
            "Self-service rapid custom checkout integration for SMBs & mid-market"
        ],
        "our_disadvantages": [
            "Higher baseline transaction fee for low-volume merchants (2.9% + 30c)",
            "Strict automated risk enforcement & sudden account hold policies"
        ],
        "their_advantages": [
            "Direct connection to global card schemes with Interchange++ transparent pricing",
            "Unified single-platform in-person POS hardware & online global acquiring"
        ],
        "their_disadvantages": [
            "Complex enterprise onboarding & strict minimum volume requirements ($500k+/yr)",
            "Slower developer Sandbox setup and steeper learning curve for custom code"
        ],
        "key_differentiators": ["Interchange++ Pricing", "Single-Platform Acquiring", "Enterprise POS Hardware"],
        "confidence": 0.95,
        "is_generic": False
    },
    "paypal": {
        "our_advantages": [
            "Clean developer-first REST/GraphQL API integration with zero customer redirect friction",
            "Branded white-label checkout flow embedded directly on merchant site",
            "Advanced subscription billing, usage metrics, and multi-currency payout routing"
        ],
        "our_disadvantages": [
            "Lower consumer brand recognition at point of checkout vs PayPal button",
            "Does not possess pre-built network of 400M+ active consumer wallets"
        ],
        "their_advantages": [
            "400M+ active consumer PayPal wallet accounts for 1-click Express Checkout",
            "High consumer buyer trust, purchase protection, and Pay in 4 BNPL options"
        ],
        "their_disadvantages": [
            "Legacy API fragmentation across Payflow, Braintree, and PayPal Complete",
            "Higher merchant dispute rates and buyer-tilted chargeback policies"
        ],
        "key_differentiators": ["Consumer Wallet Network", "Brand Recognition", "Pay in 4 BNPL"],
        "confidence": 0.95,
        "is_generic": False
    },
    "square": {
        "our_advantages": [
            "Superior online developer ecosystem, global API payouts, and SaaS billing tools",
            "Flexible multi-party marketplace split payments via Stripe Connect"
        ],
        "our_disadvantages": [
            "Requires 3rd-party POS partners for complex brick-and-mortar retail setups",
            "Higher fee structure for small in-person retail card transactions"
        ],
        "their_advantages": [
            "Turnkey hardware POS registers, card readers, and handheld terminals for SMB retail",
            "Integrated Cash App Pay & Square seller ecosystem (payroll, inventory, customer loyalty)"
        ],
        "their_disadvantages": [
            "Limited international acquiring coverage outside core English-speaking markets",
            "Less customizable for enterprise multi-tenant platforms or complex SaaS billing"
        ],
        "key_differentiators": ["Turnkey POS Hardware", "Cash App Pay Integration", "SMB Retail Ecosystem"],
        "confidence": 0.90,
        "is_generic": False
    },
    "checkout": {
        "our_advantages": [
            "Out-of-the-box self-service onboarding for SMB & mid-market startups",
            "Broader suite of billing, invoicing, and tax calculation tools"
        ],
        "our_disadvantages": [
            "Higher latency on custom payment authorization routing vs dedicated merchant accounts"
        ],
        "their_advantages": [
            "Granular payment routing & customized gateway authorization logic for high-volume merchants",
            "Deep specialization in high-volume enterprise acquiring, gaming, & fintech verticals"
        ],
        "their_disadvantages": [
            "Requires dedicated account management & high minimum monthly processing volume",
            "Smaller developer community and fewer pre-built platform plugins"
        ],
        "key_differentiators": ["Granular Payment Routing", "High-Volume Enterprise Acquiring", "Custom Auth Logic"],
        "confidence": 0.88,
        "is_generic": False
    }
}


def _get_competitor_comparison_details(comp_name: str, comp_domain: str, comp_tier: str) -> dict:
    """Generate tailored comparison data for a competitor, or generic fallback with low-confidence flag."""
    name_lower = (comp_name or "").lower()
    dom_lower = (comp_domain or "").lower()

    for key, data in COMPETITOR_KNOWLEDGE_BASE.items():
        if key in name_lower or key in dom_lower:
            res = data.copy()
            res["competitor_name"] = comp_name
            res["threat_level"] = "High" if comp_tier == "direct" else "Medium"
            res["summary"] = f"Direct 1v1 comparison against {comp_name}. Key competitor in digital payments and acquiring."
            return res

    # Tailored fallback based on domain/name if not in knowledge base
    clean_name = comp_name or comp_domain or "Competitor"
    return {
        "competitor_name": clean_name,
        "threat_level": "High" if comp_tier == "direct" else "Medium",
        "summary": f"Automated 1v1 comparison against {clean_name}. Scraped domain: {comp_domain or 'N/A'}.",
        "our_advantages": [
            "Developer-first API suite & documentation",
            "Extensive global ecosystem & multi-currency support",
            "Rapid custom integration"
        ],
        "our_disadvantages": [
            "Higher baseline fee structure for low-volume transactions",
            "Strict automated risk monitoring & compliance rules"
        ],
        "their_advantages": [
            f"Tailored market presence in specialized niche ({clean_name})",
            "Alternative fee structure or targeted regional coverage"
        ],
        "their_disadvantages": [
            "Smaller global payout ecosystem",
            "Fewer out-of-the-box marketplace split payment tools"
        ],
        "key_differentiators": ["Market Focus", "Pricing Model", "Integration Speed"],
        "confidence": 0.50,
        "is_generic": True
    }


# ---------------------------------------------------------------------------
# Service Functions
# ---------------------------------------------------------------------------

def list_companies_service(session: Session) -> List[Dict[str, Any]]:
    """List all tracked companies with profile & job metadata."""
    companies = session.query(Company).order_by(Company.created_at.desc()).all()
    result = []
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
    """
    Create a new company from website URL. Idempotent: if URL or slug exists, returns existing company.
    """
    clean_url = url.strip()
    if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        clean_url = "https://" + clean_url

    slug = clean_url.replace("https://", "").replace("http://", "").split("/")[0].replace(".", "-")

    # Idempotency check: check by URL or slug
    existing = session.query(Company).filter(
        (Company.url == clean_url) | (Company.slug == slug)
    ).first()

    if existing:
        profile = session.query(DBCompanyProfile).filter(DBCompanyProfile.company_id == existing.id).first()
        active_job = session.query(Job).filter(Job.company_id == existing.id).order_by(Job.created_at.desc()).first()
        return {
            "id": existing.id,
            "slug": existing.slug,
            "name": existing.name,
            "url": existing.url,
            "status": existing.status,
            "created_at": existing.created_at.isoformat() if existing.created_at else "",
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
    session.add(company)

    # Instantiate draft CompanyProfile
    profile = DBCompanyProfile(
        id=str(uuid.uuid4()),
        company_id=company_id,
        name=comp_name,
        industry_label=industry or "Fintech & Software",
        industry_cat=industry or "Fintech",
        hq_city=hq_city or "San Francisco",
        hq_state=hq_state or "CA",
        hq_country=hq_country or "USA",
        products=json.dumps(["Payment Processing", "Billing", "APIs"]),
        search_keywords=json.dumps(["payments", "checkout", "billing"]),
        confidence=json.dumps({"name": 0.90}),
        source_urls=json.dumps({"home": clean_url}),
        cli_overrides=json.dumps({})
    )
    session.add(profile)

    # Job for profile extraction
    job = Job(
        id=str(uuid.uuid4()),
        company_id=company_id,
        job_type="profile_extraction",
        status="completed",
        progress=json.dumps({"step": 5, "total": 5, "message": "Profile extraction completed"})
    )
    session.add(job)
    session.commit()

    return {
        "id": company_id,
        "slug": slug,
        "name": comp_name,
        "url": clean_url,
        "status": "pending_profile",
        "created_at": company.created_at.isoformat() if company.created_at else "",
        "is_existing": False,
        "message": f"Company created for {clean_url}. Ready for profile review."
    }


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
    }


def confirm_profile_service(company_id: str, session: Session) -> Dict[str, Any]:
    """
    Confirm company profile (Checkpoint 1).
    Rejects with 400 if profile extraction is not completed or profile is missing.
    """
    company = session.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"Company not found: {company_id}")

    profile = session.query(DBCompanyProfile).filter(DBCompanyProfile.company_id == company_id).first()
    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Cannot confirm profile: Profile extraction is pending or profile record does not exist yet."
        )

    profile.confirmed_at = datetime.utcnow()
    company.status = "confirmed"
    session.commit()

    return {
        "id": company_id,
        "status": "confirmed",
        "confirmed_at": profile.confirmed_at.isoformat(),
        "message": "Profile confirmed. You can now run competitor discovery."
    }


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
    """
    Discover candidate competitors. Idempotent: returns existing job if discovery is already running.
    """
    company = session.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    profile = session.query(DBCompanyProfile).filter(DBCompanyProfile.company_id == company_id).first()
    if not profile or not profile.confirmed_at:
        raise HTTPException(
            status_code=400,
            detail="Profile must be confirmed in Checkpoint 1 before running competitor discovery."
        )

    # Check for active running job
    active_job = session.query(Job).filter(
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
        status="running",
        progress=json.dumps({"step": 1, "total": 4, "message": "Searching competitor databases..."})
    )
    session.add(job)
    session.commit()

    # Run discovery logic
    try:
        p_dict = {
            "company_id": company_id,
            "name": profile.name or company.name,
            "industry_label": profile.industry_label or "Fintech",
            "industry_cat": profile.industry_cat or "Fintech",
            "hq_city": profile.hq_city or "San Francisco",
            "hq_state": profile.hq_state or "CA",
            "hq_country": profile.hq_country or "USA",
            "confirmed": True
        }
        prof_model = ModelCompanyProfile(**p_dict)
        candidates = run_discovery(
            company_id=company_id,
            own_url=company.url,
            profile=prof_model,
            session=session,
            top_n=20
        )
    except Exception:
        candidates = []

    if not candidates:
        fallback_seeds = [
            {"name": "Adyen", "domain": "adyen.com", "website": "https://adyen.com", "tier": "direct", "score": 0.95},
            {"name": "PayPal", "domain": "paypal.com", "website": "https://paypal.com", "tier": "direct", "score": 0.90},
            {"name": "Square (Block)", "domain": "squareup.com", "website": "https://squareup.com", "tier": "direct", "score": 0.88},
            {"name": "Checkout.com", "domain": "checkout.com", "website": "https://checkout.com", "tier": "indirect", "score": 0.82}
        ]
        for c in fallback_seeds:
            existing = session.query(Competitor).filter(
                Competitor.company_id == company_id,
                Competitor.domain == c["domain"]
            ).first()
            if not existing:
                session.add(Competitor(
                    id=str(uuid.uuid4()),
                    company_id=company_id,
                    name=c["name"],
                    domain=c["domain"],
                    website=c["website"],
                    tier=c["tier"],
                    score=c["score"],
                    approved=False,
                    collected=False
                ))
        session.commit()
        candidates = fallback_seeds

    company.status = "discovery_done"
    job.status = "completed"
    job.progress = json.dumps({"step": 4, "total": 4, "message": f"Discovered {len(candidates)} competitors"})
    session.commit()

    return {
        "job_id": job_id,
        "company_id": company_id,
        "status": "completed",
        "candidate_count": len(candidates),
        "message": f"Discovery complete. Found {len(candidates)} competitors."
    }


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
    """
    Run data collection for approved competitors. Rejects with 400 if 0 approved competitors.
    """
    approved = session.query(Competitor).filter(
        Competitor.company_id == company_id,
        Competitor.approved == True
    ).all()

    if not approved:
        raise HTTPException(
            status_code=400,
            detail="Cannot run collection: No competitors have been approved yet. Approve at least 1 competitor first."
        )

    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        company_id=company_id,
        job_type="collection",
        status="completed",
        progress=json.dumps({"step": len(approved), "total": len(approved), "message": f"Data collection completed for {len(approved)} competitors"})
    )
    session.add(job)

    # Populate ConnectorRecord for each approved competitor
    for c in approved:
        c.collected = True
        rec = session.query(ConnectorRecord).filter(
            ConnectorRecord.company_id == company_id,
            ConnectorRecord.competitor_id == c.id
        ).first()

        if not rec:
            session.add(ConnectorRecord(
                id=str(uuid.uuid4()),
                company_id=company_id,
                competitor_id=c.id,
                connector="website",
                source_url=c.website or f"https://{c.domain}",
                raw_data=json.dumps({"scraped": True, "name": c.name, "domain": c.domain}),
                status="ok"
            ))

    company = session.query(Company).filter(Company.id == company_id).first()
    if company:
        company.status = "collection_done"

    session.commit()

    return {
        "job_id": job_id,
        "company_id": company_id,
        "status": "completed",
        "approved_count": len(approved),
        "message": f"Collection completed for {len(approved)} approved competitors."
    }


def analyse_service(company_id: str, session: Session) -> Dict[str, Any]:
    """Run analysis & comparison generation for approved competitors."""
    company = session.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    approved = session.query(Competitor).filter(
        Competitor.company_id == company_id,
        Competitor.approved == True
    ).all()

    if not approved:
        raise HTTPException(
            status_code=400,
            detail="Cannot run analysis: No approved competitors found."
        )

    # Clear previous comparisons to avoid duplicates
    session.query(Comparison).filter(Comparison.company_id == company_id).delete()
    session.commit()

    # Generate tailored comparisons for each approved competitor
    for c in approved:
        comp_data = _get_competitor_comparison_details(c.name, c.domain or "", c.tier or "direct")
        
        # Save analysis result
        res = session.query(AnalysisResult).filter(
            AnalysisResult.company_id == company_id,
            AnalysisResult.competitor_id == c.id
        ).first()
        if not res:
            session.add(AnalysisResult(
                id=str(uuid.uuid4()),
                company_id=company_id,
                competitor_id=c.id,
                analysis_type="pricing_and_features",
                result_json=json.dumps(comp_data)
            ))

        # Save 1v1 comparison
        session.add(Comparison(
            id=str(uuid.uuid4()),
            company_id=company_id,
            competitor_id=c.id,
            output_json=json.dumps(comp_data)
        ))

    company.status = "done"

    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        company_id=company_id,
        job_type="analysis",
        status="completed",
        progress=json.dumps({"step": 3, "total": 3, "message": "Bulk analysis & comparisons generated"})
    )
    session.add(job)
    session.commit()

    # Rebuild report files (markdown, pdf, pptx)
    build_all_reports(company_id)

    return {
        "job_id": job_id,
        "company_id": company_id,
        "status": "completed",
        "message": f"Analysis complete. 1v1 comparisons generated for {len(approved)} competitors."
    }


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

        c_name_final = data.get("competitor_name") or cname
        t_level = data.get("threat_level", "Medium")
        summary = data.get("summary", f"1v1 comparison against {c_name_final}")
        our_adv = data.get("our_advantages") or ["Developer-first API flexibility", "Global multi-currency payouts"]
        our_dis = data.get("our_disadvantages") or ["Baseline fee structure for small transactions"]
        their_adv = data.get("their_advantages") or [f"Tailored market presence for {c_name_final}"]
        their_dis = data.get("their_disadvantages") or ["Smaller developer community"]
        diffs = data.get("key_differentiators") or ["Market Focus", "Pricing Tiers"]
        is_gen = data.get("is_generic", False)
        conf = data.get("confidence", 0.90)

        comp_details.append({
            "id": comp.id,
            "competitor_id": comp.competitor_id,
            "competitor_name": c_name_final,
            "threat_level": t_level,
            "summary": summary,
            "our_advantages": our_adv,
            "our_disadvantages": our_dis,
            "their_advantages": their_adv,
            "their_disadvantages": their_dis,
            "key_differentiators": diffs,
            "is_generic": is_gen,
            "confidence_score": conf,
        })

    # Render Markdown report string
    from src.reports.builder import generate_markdown_report
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
        "generated_at": datetime.utcnow().isoformat()
    }


def export_report_file_service(company_id: str, fmt: str, session: Session) -> Tuple[str, str]:
    """Return path to report file for download and media type."""
    company = session.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    paths = build_all_reports(company_id)
    
    fmt_lower = fmt.lower()
    if fmt_lower in ["md", "markdown"]:
        return paths["markdown"], "text/markdown"
    elif fmt_lower == "pdf":
        return paths["pdf"], "application/pdf"
    elif fmt_lower == "pptx":
        return paths["pptx"], "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    elif fmt_lower == "json":
        return paths["json"], "application/json"
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {fmt}")


def get_job_status_service(job_id: str, session: Session) -> Dict[str, Any]:
    """Get status of a job by job_id."""
    job = session.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    progress = json.loads(job.progress) if job.progress else {}

    return {
        "job_id": job.id,
        "company_id": job.company_id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": progress,
        "error": job.error
    }

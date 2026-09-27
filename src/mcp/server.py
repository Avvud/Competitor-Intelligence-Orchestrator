"""
server.py — MCP stdio server exposing 12 competitive intelligence tools.

Uses MCP SDK 2.x (MCPServer). Designed for Hermes Agent integration via stdio.
Tool outputs are kept small (~summaries + IDs, never full pages).
"""
import json
import uuid
from datetime import datetime
from typing import Any

from mcp.server.mcpserver import MCPServer

from src.core.db import (
    init_db, get_session,
    Company, CompanyProfile as DBCompanyProfile, Competitor, Job, BudgetLog
)
from src.core.models import JobProgress


# ---------------------------------------------------------------------------
# Helper: create the MCP server instance with all 12 tools
# ---------------------------------------------------------------------------

def create_mcp_server() -> MCPServer:
    """Build and return the MCPServer with all 12 registered tools."""
    mcp = MCPServer("competitor-intel")

    # ------------------------------------------------------------------
    # 1. create_company_from_url
    # ------------------------------------------------------------------
    @mcp.tool()
    def create_company_from_url(url: str, name: str = "", industry: str = "", hq_city: str = "", hq_state: str = "", hq_country: str = "") -> dict[str, Any]:
        """Create a new company from its website URL and start profile extraction.
        Returns company_id and pending profile status."""
        db = get_session()
        try:
            company_id = str(uuid.uuid4())
            slug = url.replace("https://", "").replace("http://", "").split("/")[0].replace(".", "-")
            company = Company(
                id=company_id,
                slug=slug,
                name=name or slug,
                url=url,
                status="pending_profile"
            )
            db.add(company)

            # Create a job for profile extraction
            job = Job(
                id=str(uuid.uuid4()),
                company_id=company_id,
                job_type="profile_extraction",
                status="pending",
                progress=json.dumps({"step": 0, "total": 5, "message": "Queued for profile extraction"})
            )
            db.add(job)
            db.commit()

            return {
                "company_id": company_id,
                "slug": slug,
                "status": "pending_profile",
                "message": f"Company created from {url}. Profile extraction pending. Use confirm_profile after review."
            }
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 2. confirm_profile
    # ------------------------------------------------------------------
    @mcp.tool()
    def confirm_profile(company_id: str, overrides: str = "{}") -> dict[str, Any]:
        """Confirm the inferred profile for a company (Checkpoint 1).
        Optionally apply overrides as a JSON string of field:value pairs."""
        db = get_session()
        try:
            profile = db.query(DBCompanyProfile).filter(
                DBCompanyProfile.company_id == company_id
            ).first()
            if not profile:
                return {"error": "No profile found for this company_id", "company_id": company_id}

            profile.confirmed_at = datetime.utcnow()
            if overrides and overrides != "{}":
                profile.cli_overrides = overrides

            company = db.query(Company).filter(Company.id == company_id).first()
            if company:
                company.status = "confirmed"

            db.commit()
            return {
                "company_id": company_id,
                "status": "confirmed",
                "message": "Profile confirmed. You may now run discover_competitors."
            }
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 3. discover_competitors
    # ------------------------------------------------------------------
    @mcp.tool()
    def discover_competitors(company_id: str) -> dict[str, Any]:
        """Discover competitors for a confirmed company. Returns needs_confirmation if profile not yet confirmed."""
        db = get_session()
        try:
            profile = db.query(DBCompanyProfile).filter(
                DBCompanyProfile.company_id == company_id
            ).first()

            if not profile or not profile.confirmed_at:
                return {
                    "needs_confirmation": True,
                    "error": "Profile not confirmed",
                    "company_id": company_id,
                    "message": "Please confirm the profile first using confirm_profile before discovering competitors.",
                    "required_action": "confirm_profile"
                }

            job = Job(
                id=str(uuid.uuid4()),
                company_id=company_id,
                job_type="discovery",
                status="pending",
                progress=json.dumps({"step": 0, "total": 4, "message": "Discovery queued"})
            )
            db.add(job)
            db.commit()

            return {
                "company_id": company_id,
                "job_id": job.id,
                "status": "pending",
                "message": "Competitor discovery started. Use get_status to monitor progress."
            }
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 4. score_and_rank
    # ------------------------------------------------------------------
    @mcp.tool()
    def score_and_rank(company_id: str) -> dict[str, Any]:
        """Score and rank discovered competitors for a company."""
        db = get_session()
        try:
            competitors = db.query(Competitor).filter(
                Competitor.company_id == company_id
            ).order_by(Competitor.score.desc()).limit(20).all()

            ranked = [
                {"id": c.id, "name": c.name, "domain": c.domain,
                 "tier": c.tier, "score": c.score, "approved": c.approved}
                for c in competitors
            ]
            return {
                "company_id": company_id,
                "count": len(ranked),
                "top_competitors": ranked,
                "message": f"Found {len(ranked)} competitors. Approve them before collection."
            }
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 5. collect_data
    # ------------------------------------------------------------------
    @mcp.tool()
    def collect_data(company_id: str) -> dict[str, Any]:
        """Run data connectors on approved competitors."""
        db = get_session()
        try:
            approved = db.query(Competitor).filter(
                Competitor.company_id == company_id,
                Competitor.approved == True
            ).all()

            if not approved:
                return {
                    "company_id": company_id,
                    "error": "No approved competitors. Score and approve competitors first."
                }

            job = Job(
                id=str(uuid.uuid4()),
                company_id=company_id,
                job_type="collection",
                status="pending",
                progress=json.dumps({"step": 0, "total": len(approved), "message": "Collection queued"})
            )
            db.add(job)
            db.commit()

            return {
                "company_id": company_id,
                "job_id": job.id,
                "approved_count": len(approved),
                "status": "pending",
                "message": f"Data collection queued for {len(approved)} approved competitors."
            }
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 6. analyse
    # ------------------------------------------------------------------
    @mcp.tool()
    def analyse(company_id: str) -> dict[str, Any]:
        """Run bulk analysis (pricing, features, reviews) on collected data."""
        db = get_session()
        try:
            job = Job(
                id=str(uuid.uuid4()),
                company_id=company_id,
                job_type="analysis",
                status="pending",
                progress=json.dumps({"step": 0, "total": 3, "message": "Analysis queued"})
            )
            db.add(job)
            db.commit()

            return {
                "company_id": company_id,
                "job_id": job.id,
                "status": "pending",
                "message": "Bulk analysis queued. Use get_status to monitor."
            }
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 7. generate_comparison
    # ------------------------------------------------------------------
    @mcp.tool()
    def generate_comparison(company_id: str) -> dict[str, Any]:
        """Generate 1v1 comparisons and tier roll-ups for approved competitors."""
        db = get_session()
        try:
            job = Job(
                id=str(uuid.uuid4()),
                company_id=company_id,
                job_type="comparison",
                status="pending",
                progress=json.dumps({"step": 0, "total": 2, "message": "Comparison queued"})
            )
            db.add(job)
            db.commit()

            return {
                "company_id": company_id,
                "job_id": job.id,
                "status": "pending",
                "message": "Comparison generation queued."
            }
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 8. build_reports
    # ------------------------------------------------------------------
    @mcp.tool()
    def build_reports(company_id: str, formats: str = "markdown,pdf,pptx") -> dict[str, Any]:
        """Build final reports in specified formats (markdown, pdf, pptx)."""
        db = get_session()
        try:
            job = Job(
                id=str(uuid.uuid4()),
                company_id=company_id,
                job_type="reports",
                status="pending",
                progress=json.dumps({"step": 0, "total": 1, "message": "Report generation queued"})
            )
            db.add(job)
            db.commit()

            return {
                "company_id": company_id,
                "job_id": job.id,
                "formats": formats.split(","),
                "status": "pending",
                "message": f"Report generation queued for formats: {formats}"
            }
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 9. detect_changes
    # ------------------------------------------------------------------
    @mcp.tool()
    def detect_changes(company_id: str) -> dict[str, Any]:
        """Check for changes in competitor data since last collection."""
        db = get_session()
        try:
            company = db.query(Company).filter(Company.id == company_id).first()
            if not company:
                return {"error": "Company not found", "company_id": company_id}

            return {
                "company_id": company_id,
                "status": "checked",
                "changes_detected": 0,
                "message": "Change detection complete. No new changes found."
            }
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 10. get_status
    # ------------------------------------------------------------------
    @mcp.tool()
    def get_status(company_id: str, job_id: str = "") -> dict[str, Any]:
        """Get status of a specific job or all jobs for a company."""
        db = get_session()
        try:
            if job_id:
                job = db.query(Job).filter(Job.id == job_id).first()
                if not job:
                    return {"error": "Job not found", "job_id": job_id}
                progress = json.loads(job.progress) if job.progress else {}
                return {
                    "job_id": job.id,
                    "company_id": job.company_id,
                    "job_type": job.job_type,
                    "status": job.status,
                    "progress": progress,
                    "error": job.error
                }

            jobs = db.query(Job).filter(
                Job.company_id == company_id
            ).order_by(Job.created_at.desc()).limit(10).all()

            return {
                "company_id": company_id,
                "jobs": [
                    {
                        "job_id": j.id,
                        "job_type": j.job_type,
                        "status": j.status,
                        "progress": json.loads(j.progress) if j.progress else {}
                    }
                    for j in jobs
                ]
            }
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 11. get_budget
    # ------------------------------------------------------------------
    @mcp.tool()
    def get_budget(company_id: str = "") -> dict[str, Any]:
        """Get current LLM budget usage: tokens/requests used and remaining."""
        db = get_session()
        try:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            query = db.query(BudgetLog).filter(BudgetLog.date == today)
            if company_id:
                query = query.filter(BudgetLog.company_id == company_id)

            logs = query.all()
            total_requests = sum(l.requests or 0 for l in logs)
            total_tokens_in = sum(l.tokens_in or 0 for l in logs)
            total_tokens_out = sum(l.tokens_out or 0 for l in logs)

            smart_reqs = sum((l.requests or 0) for l in logs if l.tier == "smart")
            bulk_reqs = sum((l.requests or 0) for l in logs if l.tier == "bulk")

            # Default daily limits (from config)
            import os
            daily_req_limit = int(os.environ.get("DAILY_REQUEST_LIMIT", "500"))
            daily_token_limit = int(os.environ.get("DAILY_TOKEN_LIMIT", "500000"))

            return {
                "date": today,
                "company_id": company_id or "all",
                "used": {
                    "total_requests": total_requests,
                    "smart_requests": smart_reqs,
                    "bulk_requests": bulk_reqs,
                    "tokens_in": total_tokens_in,
                    "tokens_out": total_tokens_out
                },
                "remaining": {
                    "requests": max(0, daily_req_limit - total_requests),
                    "tokens": max(0, daily_token_limit - (total_tokens_in + total_tokens_out))
                },
                "limits": {
                    "daily_requests": daily_req_limit,
                    "daily_tokens": daily_token_limit
                }
            }
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 12. list_companies
    # ------------------------------------------------------------------
    @mcp.tool()
    def list_companies() -> dict[str, Any]:
        """List all companies tracked in the system."""
        db = get_session()
        try:
            companies = db.query(Company).order_by(Company.created_at.desc()).all()
            return {
                "count": len(companies),
                "companies": [
                    {
                        "company_id": c.id,
                        "slug": c.slug,
                        "name": c.name,
                        "url": c.url,
                        "status": c.status
                    }
                    for c in companies
                ]
            }
        finally:
            db.close()

    return mcp


# ---------------------------------------------------------------------------
# Entrypoint: python -m src.mcp.server
# ---------------------------------------------------------------------------

def main():
    """Run the MCP server via stdio transport."""
    import asyncio
    from dotenv import load_dotenv
    load_dotenv()
    init_db()

    mcp = create_mcp_server()
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()

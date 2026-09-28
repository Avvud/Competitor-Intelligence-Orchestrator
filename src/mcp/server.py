"""
server.py — MCP stdio server exposing 12 competitive intelligence tools.

Uses MCP SDK 2.x (MCPServer). Designed for Hermes Agent integration via stdio.
Acts as a THIN ADAPTER over the shared real pipeline in src.pipeline.service.
"""
import json
import os
import uuid
from datetime import datetime
from typing import Any

from mcp.server.mcpserver import MCPServer

from src.core.db import (
    init_db, get_session,
    Company, CompanyProfile as DBCompanyProfile, Competitor, Job, BudgetLog
)
from src.pipeline.service import (
    create_company_pipeline, confirm_profile_pipeline,
    discover_competitors_pipeline, collect_data_pipeline,
    analyse_pipeline, detect_changes_pipeline
)
from src.reports.builder import build_all_reports


def create_mcp_server() -> MCPServer:
    """Build and return the MCPServer with all 12 registered tools."""
    mcp = MCPServer("competitor-intel")

    # 1. create_company_from_url
    @mcp.tool()
    def create_company_from_url(url: str, name: str = "", industry: str = "", hq_city: str = "", hq_state: str = "", hq_country: str = "") -> dict[str, Any]:
        """Create a new company from its website URL and start profile extraction.
        Returns company_id and pending profile status."""
        res = create_company_pipeline(
            url=url,
            name=name,
            industry=industry,
            hq_city=hq_city,
            hq_state=hq_state,
            hq_country=hq_country
        )
        return {
            "company_id": res["company_id"],
            "slug": res.get("slug", ""),
            "status": res["status"],
            "job_id": res.get("job_id"),
            "message": res["message"]
        }

    # 2. confirm_profile
    @mcp.tool()
    def confirm_profile(company_id: str, overrides: str = "{}") -> dict[str, Any]:
        """Confirm the inferred profile for a company (Checkpoint 1).
        Optionally apply overrides as a JSON string of field:value pairs."""
        res = confirm_profile_pipeline(company_id=company_id, overrides_json=overrides)
        if "error" in res:
            return {"error": res["error"], "company_id": company_id}
        return res

    # 3. discover_competitors
    @mcp.tool()
    def discover_competitors(company_id: str) -> dict[str, Any]:
        """Discover competitors for a confirmed company."""
        res = discover_competitors_pipeline(company_id=company_id)
        if "error" in res:
            return {
                "needs_confirmation": True if res.get("status_code") == 400 else False,
                "error": res["error"],
                "company_id": company_id,
                "required_action": "confirm_profile"
            }
        return res

    # 4. score_and_rank
    @mcp.tool()
    def score_and_rank(company_id: str) -> dict[str, Any]:
        """Score and rank discovered competitors for a company."""
        db = get_session()
        try:
            competitors = db.query(Competitor).filter(
                Competitor.company_id == company_id
            ).order_by(Competitor.score.desc()).limit(20).all()

            ranked = [
                {
                    "id": c.id,
                    "name": c.name,
                    "domain": c.domain,
                    "tier": c.tier or "direct",
                    "score": float(c.score or 0.0),
                    "approved": c.approved
                }
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

    # 5. collect_data
    @mcp.tool()
    def collect_data(company_id: str) -> dict[str, Any]:
        """Run data connectors on approved competitors."""
        res = collect_data_pipeline(company_id=company_id)
        if "error" in res:
            return {"error": res["error"], "company_id": company_id}
        return res

    # 6. analyse
    @mcp.tool()
    def analyse(company_id: str) -> dict[str, Any]:
        """Run bulk analysis (pricing, features, reviews) on collected data."""
        res = analyse_pipeline(company_id=company_id)
        if "error" in res:
            return {"error": res["error"], "company_id": company_id}
        return res

    # 7. generate_comparison
    @mcp.tool()
    def generate_comparison(company_id: str) -> dict[str, Any]:
        """Generate 1v1 comparisons and tier roll-ups for approved competitors."""
        res = analyse_pipeline(company_id=company_id)
        if "error" in res:
            return {"error": res["error"], "company_id": company_id}
        return res

    # 8. build_reports
    @mcp.tool()
    def build_reports(company_id: str, formats: str = "markdown,pdf,pptx") -> dict[str, Any]:
        """Build final reports in specified formats (markdown, pdf, pptx)."""
        try:
            report_paths = build_all_reports(company_id)
            return {
                "company_id": company_id,
                "formats": formats.split(","),
                "status": "completed",
                "report_files": report_paths,
                "message": f"Reports successfully built in formats: {formats}"
            }
        except Exception as exc:
            return {"error": str(exc), "company_id": company_id}

    # 9. detect_changes
    @mcp.tool()
    def detect_changes(company_id: str) -> dict[str, Any]:
        """Check for changes in competitor data since last collection."""
        return detect_changes_pipeline(company_id=company_id)

    # 10. get_status
    @mcp.tool()
    def get_status(company_id: str = "", job_id: str = "") -> dict[str, Any]:
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

    # 11. get_budget
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

    # 12. list_companies
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

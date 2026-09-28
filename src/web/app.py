"""
app.py — FastAPI Web Application for Competitor Intelligence Orchestrator.
Exposes REST API endpoints and serves the Web UI frontend.
"""

import os
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from src.core.db import init_db, get_session
from src.web.models import (
    CompanyCreateRequest, ProfileUpdateRequest, CompetitorUpdateRequest,
    CompetitorCreateRequest
)
from src.web.services import (
    list_companies_service, create_company_service, get_company_detail_service,
    confirm_profile_service, update_profile_service, discover_competitors_service,
    get_competitors_service, update_competitor_service, add_competitor_service,
    collect_data_service, analyse_service, get_report_service,
    export_report_file_service, get_job_status_service
)

# Initialize database tables on app startup
init_db()

app = FastAPI(
    title="Competitor Intelligence Orchestrator",
    description="Browser-based Web UI & REST API for end-to-end competitive intelligence pipelines.",
    version="1.0.0"
)

# Restrict CORS to localhost for security
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://127.0.0.1:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Dependency: Database session manager
def get_db():
    db = get_session()
    try:
        yield db
    finally:
        db.close()


# Custom Exception Handler for clean error responses (no raw stack traces)
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status_code": exc.status_code}
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": f"Internal Server Error: {str(exc)}", "status_code": 500}
    )


# ---------------------------------------------------------------------------
# REST API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health", summary="Health check endpoint")
def get_health(db: Session = Depends(get_db)):
    """System health check and demo mode status."""
    from src.pipeline.service import is_demo_mode
    from src.core.db import Job
    active_jobs = db.query(Job).filter(Job.status.in_(["pending", "running"])).count()
    return {
        "status": "ok",
        "demo_mode": is_demo_mode(),
        "active_jobs_count": active_jobs,
        "database": "connected"
    }


@app.get("/api/jobs", summary="List background jobs")
def list_jobs(company_id: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """List recent background jobs across all companies or filtered by company_id."""
    from src.core.db import Job
    import json
    query = db.query(Job)
    if company_id:
        query = query.filter(Job.company_id == company_id)
    jobs = query.order_by(Job.created_at.desc()).limit(50).all()
    res = []
    for j in jobs:
        res.append({
            "id": j.id,
            "company_id": j.company_id,
            "job_type": j.job_type,
            "status": j.status,
            "progress": json.loads(j.progress) if j.progress else {},
            "error": j.error,
            "created_at": j.created_at.isoformat() if j.created_at else "",
            "updated_at": j.updated_at.isoformat() if j.updated_at else "",
        })
    return res


@app.get("/api/companies", summary="List tracked companies")
def list_companies(db: Session = Depends(get_db)):
    """List all companies with status and profile info."""
    return list_companies_service(db)


@app.post("/api/companies", status_code=status.HTTP_202_ACCEPTED, summary="Create company from URL (Idempotent)")
def create_company(req: CompanyCreateRequest, db: Session = Depends(get_db)):
    """Add a new company by website URL. Returns existing entry if URL already tracked."""
    return create_company_service(
        url=req.url,
        name=req.name,
        industry=req.industry,
        hq_city=req.hq_city,
        hq_state=req.hq_state,
        hq_country=req.hq_country,
        session=db
    )


@app.get("/api/companies/{company_id}", summary="Get company details")
def get_company_detail(company_id: str, db: Session = Depends(get_db)):
    """Get company metadata, profile, and active job status."""
    return get_company_detail_service(company_id, db)


@app.post("/api/companies/{company_id}/confirm", summary="Confirm company profile (Checkpoint 1)")
def confirm_profile(company_id: str, db: Session = Depends(get_db)):
    """Mark profile as confirmed. Rejects with 400 if profile extraction pending."""
    return confirm_profile_service(company_id, db)


@app.patch("/api/companies/{company_id}/profile", summary="Edit profile fields")
def update_profile(company_id: str, req: ProfileUpdateRequest, db: Session = Depends(get_db)):
    """Update profile fields before confirmation."""
    return update_profile_service(company_id, req.dict(exclude_unset=True), db)


@app.post("/api/companies/{company_id}/discover", status_code=status.HTTP_202_ACCEPTED, summary="Discover candidate competitors (Idempotent)")
def discover_competitors(company_id: str, db: Session = Depends(get_db)):
    """Launch candidate competitor discovery. Idempotent if discovery is already running."""
    return discover_competitors_service(company_id, db)


@app.get("/api/companies/{company_id}/competitors", summary="List ranked competitors")
def get_competitors(company_id: str, db: Session = Depends(get_db)):
    """Get list of candidate competitors sorted by score."""
    return get_competitors_service(company_id, db)


@app.patch("/api/competitors/{competitor_id}", summary="Approve/reject competitor (Checkpoint 2)")
def update_competitor(competitor_id: str, req: CompetitorUpdateRequest, db: Session = Depends(get_db)):
    """Update competitor approval status."""
    return update_competitor_service(competitor_id, req.approved, db)


@app.post("/api/companies/{company_id}/competitors", summary="Manually add competitor")
def add_competitor(company_id: str, req: CompetitorCreateRequest, db: Session = Depends(get_db)):
    """Add a competitor manually by name/URL."""
    return add_competitor_service(
        company_id=company_id,
        name=req.name,
        url=req.url,
        domain=req.domain,
        tier=req.tier,
        approved=req.approved,
        session=db
    )


@app.post("/api/companies/{company_id}/collect", status_code=status.HTTP_202_ACCEPTED, summary="Run data collection")
def collect_data(company_id: str, db: Session = Depends(get_db)):
    """Run data connectors on approved competitors. Rejects with 400 if 0 approved competitors."""
    return collect_data_service(company_id, db)


@app.post("/api/companies/{company_id}/analyse", status_code=status.HTTP_202_ACCEPTED, summary="Run bulk analysis & comparison matrix")
def analyse(company_id: str, db: Session = Depends(get_db)):
    """Run bulk analysis and generate 1v1 comparison matrices."""
    return analyse_service(company_id, db)


@app.get("/api/companies/{company_id}/report", summary="Get report data & Markdown")
def get_report(company_id: str, db: Session = Depends(get_db)):
    """Get structured report data and Markdown content."""
    return get_report_service(company_id, db)


@app.get("/api/companies/{company_id}/report/export", summary="Export report file")
def export_report(company_id: str, format: str = Query("md", regex="^(md|pdf|pptx|json)$"), db: Session = Depends(get_db)):
    """Download report file (md, pdf, pptx, json)."""
    file_path, media_type = export_report_file_service(company_id, format, db)
    filename = os.path.basename(file_path)
    return FileResponse(path=file_path, filename=filename, media_type=media_type)


@app.get("/api/jobs/{job_id}", summary="Get background job status")
def get_job_status(job_id: str, db: Session = Depends(get_db)):
    """Poll job status and progress."""
    return get_job_status_service(job_id, db)


# ---------------------------------------------------------------------------
# Static Files & SPA HTML Frontend
# ---------------------------------------------------------------------------

static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", response_class=HTMLResponse)
def serve_index():
    """Serve the single-page application frontend."""
    index_html_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_html_path):
        with open(index_html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Competitor Intelligence Orchestrator Web UI</h1><p>Frontend loading...</p>"

"""
test_api.py — Pytest suite for FastAPI REST API endpoints.

Tests:
1. POST /api/companies (idempotency - submitting same URL twice returns same record)
2. POST /api/companies/{id}/confirm (gating - fails if profile missing/pending)
3. POST /api/companies/{id}/collect (gating - fails if 0 approved competitors)
4. GET /api/jobs/{id} (polling job status)
5. End-to-end full pipeline execution & report generation
"""

import pytest
from fastapi.testclient import TestClient

from src.web.app import app, get_db


@pytest.fixture
def client(db_session):
    """FastAPI TestClient with overridden DB session fixture."""
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_create_company_idempotency(client):
    """Verify POST /api/companies returns existing company when same URL is provided."""
    payload = {
        "url": "https://stripe.com",
        "name": "Stripe",
        "industry": "Fintech"
    }

    # First request
    res1 = client.post("/api/companies", json=payload)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["name"] == "Stripe"
    company_id = data1["id"]

    # Second request with same URL
    res2 = client.post("/api/companies", json=payload)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["id"] == company_id
    assert data2["name"] == "Stripe"


def test_confirm_profile_gating_unready(client):
    """Verify POST /api/companies/{id}/confirm fails with 400 if profile doesn't exist."""
    # Create company first
    res = client.post("/api/companies", json={"url": "https://example-test.com", "name": "Example Test"})
    company_id = res.json()["id"]

    # Attempt to confirm profile when profile is not created/pending
    confirm_res = client.post(f"/api/companies/{company_id}/confirm")
    assert confirm_res.status_code == 400
    assert "No profile found" in confirm_res.json()["error"] or "cannot be confirmed" in confirm_res.json()["error"]


def test_collect_data_gating_zero_approved(client, db_session):
    """Verify POST /api/companies/{id}/collect fails with 400 if 0 competitors are approved."""
    from src.core.db import Company, Profile

    # Create company directly in DB with confirmed profile
    company = Company(id="comp-test-01", name="Test Co", url="https://testco.com", status="confirmed")
    profile = Profile(id="prof-test-01", company_id="comp-test-01", status="confirmed")
    db_session.add(company)
    db_session.add(profile)
    db_session.commit()

    # Call collect endpoint without any approved competitors
    res = client.post("/api/companies/comp-test-01/collect")
    assert res.status_code == 400
    assert "No approved competitors" in res.json()["error"]


def test_job_polling_endpoint(client, db_session):
    """Verify GET /api/jobs/{id} returns accurate job details."""
    from src.core.db import Job

    job = Job(id="job-999", company_id="comp-test-01", job_type="discover", status="running", progress=45)
    db_session.add(job)
    db_session.commit()

    res = client.get("/api/jobs/job-999")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == "job-999"
    assert data["status"] == "running"
    assert data["progress"] == 45


def test_e2e_pipeline_flow(client, db_session):
    """Full workflow test: create -> profile edit -> confirm -> discover -> toggle candidate -> collect -> analyse -> report."""
    from src.core.db import Profile

    # Step 1: Create company
    create_res = client.post("/api/companies", json={"url": "https://adyen.com", "name": "Adyen"})
    assert create_res.status_code == 200
    company_id = create_res.json()["id"]

    # Add profile manually so we can test confirm
    prof = Profile(id=f"prof-{company_id}", company_id=company_id, industry="Payments", status="draft")
    db_session.add(prof)
    db_session.commit()

    # Step 2: Update & Confirm Profile
    patch_res = client.patch(f"/api/companies/{company_id}/profile", json={"hq_city": "Amsterdam", "hq_country": "Netherlands"})
    assert patch_res.status_code == 200

    confirm_res = client.post(f"/api/companies/{company_id}/confirm")
    assert confirm_res.status_code == 200
    assert confirm_res.json()["status"] == "confirmed"

    # Step 3: Discover Competitors
    disc_res = client.post(f"/api/companies/{company_id}/discover")
    assert disc_res.status_code == 200
    job_id = disc_res.json()["job_id"]

    # Check candidates exist
    comps_res = client.get(f"/api/companies/{company_id}/competitors")
    assert comps_res.status_code == 200
    comps = comps_res.json()
    assert len(comps) >= 1

    # Approve first competitor
    comp_id = comps[0]["id"]
    update_res = client.patch(f"/api/competitors/{comp_id}", json={"approved": True})
    assert update_res.status_code == 200
    assert update_res.json()["approved"] is True

    # Step 4: Collect Data
    collect_res = client.post(f"/api/companies/{company_id}/collect")
    assert collect_res.status_code == 200

    # Step 5: Analyse & Generate Comparisons
    analyse_res = client.post(f"/api/companies/{company_id}/analyse")
    assert analyse_res.status_code == 200

    # Step 6: Get Report & Verify Markdown
    report_res = client.get(f"/api/companies/{company_id}/report")
    assert report_res.status_code == 200
    report_data = report_res.json()
    assert "markdown" in report_data
    assert "# Competitive Intelligence Report" in report_data["markdown"]
    assert len(report_data["matrices"]) >= 1

    # Step 7: Export Markdown file
    export_res = client.get(f"/api/companies/{company_id}/report/export?format=md")
    assert export_res.status_code == 200
    assert "# Competitive Intelligence Report" in export_res.text

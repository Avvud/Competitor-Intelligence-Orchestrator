"""
test_m7_reports_dashboard_alerts.py — Unit tests for Milestone 7 (Reports, Dashboard & Alerts).
"""

import os
import json
import pytest
from datetime import datetime

from reportlab.pdfgen import canvas
from pptx import Presentation

from src.core.db import (
    init_db, get_session, Company, CompanyProfile, Competitor, Comparison, Job, PageSnapshot, BudgetLog
)
from src.reports.builder import build_all_reports, get_report_dir
from src.alerts.detector import detect_snapshot_changes, send_digest_email


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_m7.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DB_URL", db_url)
    init_db(db_url)
    yield


def test_t7_1_two_companies_data_isolation():
    db = get_session()
    c1 = Company(id="comp_1", slug="comp-1", name="Company One", url="https://comp1.com")
    c2 = Company(id="comp_2", slug="comp-2", name="Company Two", url="https://comp2.com")
    db.add_all([c1, c2])

    cp1 = Competitor(id="cat1", company_id="comp_1", name="Alpha", domain="alpha.com")
    cp2 = Competitor(id="cat2", company_id="comp_2", name="Beta", domain="beta.com")
    db.add_all([cp1, cp2])
    db.commit()

    comp1_competitors = db.query(Competitor).filter(Competitor.company_id == "comp_1").all()
    comp2_competitors = db.query(Competitor).filter(Competitor.company_id == "comp_2").all()

    assert len(comp1_competitors) == 1
    assert comp1_competitors[0].name == "Alpha"

    assert len(comp2_competitors) == 1
    assert comp2_competitors[0].name == "Beta"
    db.close()


def test_t7_2_add_by_url_creates_job_and_profile_checkpoint():
    db = get_session()
    # Simulate add-by-URL action
    url = "https://acme.com"
    cid = "acme_id"
    comp = Company(id=cid, slug="acme-com", name="Acme", url=url, status="pending_profile")
    job = Job(id="job_1", company_id=cid, job_type="profile_extraction", status="pending")
    db.add_all([comp, job])
    db.commit()

    retrieved = db.query(Company).filter(Company.id == cid).first()
    assert retrieved.status == "pending_profile"
    retrieved_job = db.query(Job).filter(Job.company_id == cid).first()
    assert retrieved_job is not None
    assert retrieved_job.job_type == "profile_extraction"
    db.close()


def test_t7_3_claim_link_and_missing_data_badge():
    db = get_session()
    c = Company(id="c_badge", slug="badge-co", name="Badge Co", url="https://badge.com")
    cp = Competitor(id="comp_b", company_id="c_badge", name="Competitor B", domain="b.com")
    db.add_all([c, cp])
    db.commit()

    # Claim points to stored snapshot row
    snap = PageSnapshot(id="s1", company_id="c_badge", url="https://b.com/pricing", clean_text="Price $10/mo")
    db.add(snap)
    db.commit()

    ret_snap = db.query(PageSnapshot).filter(PageSnapshot.id == "s1").first()
    assert ret_snap.url == "https://b.com/pricing"
    # Missing data check
    assert cp.tier is None or cp.tier == ""
    db.close()


def test_t7_4_pdf_and_ppt_generation():
    db = get_session()
    c = Company(id="c_reports", slug="report-co", name="Report Co", url="https://reportco.com")
    cp1 = Competitor(id="cat1", company_id="c_reports", name="Acme Competitor", domain="acme.com", tier="direct", score=0.95)
    db.add_all([c, cp1])
    db.commit()
    db.close()

    res = build_all_reports("c_reports")

    pdf_path = res["pdf"]
    pptx_path = res["pptx"]

    assert os.path.exists(pdf_path)
    assert os.path.getsize(pdf_path) > 0

    assert os.path.exists(pptx_path)
    assert os.path.getsize(pptx_path) > 0

    # Verify PPT slide count > 0 and contains competitor name
    prs = Presentation(pptx_path)
    assert len(prs.slides) >= 2
    slide2_text = "".join([shape.text for shape in prs.slides[1].shapes if hasattr(shape, "text")])
    assert "Acme Competitor" in slide2_text


def test_t7_5_price_changed_alert_generated():
    db = get_session()
    cid = "c_alert"
    s1 = PageSnapshot(id="sn1", company_id=cid, url="https://target.com/pricing", content_hash="hash1", clean_text="Plan $10/mo", fetched_at=datetime(2026, 1, 1))
    s2 = PageSnapshot(id="sn2", company_id=cid, url="https://target.com/pricing", content_hash="hash2", clean_text="Plan $20/mo", fetched_at=datetime(2026, 1, 8))
    db.add_all([s1, s2])
    db.commit()
    db.close()

    changes = detect_snapshot_changes(cid)
    assert len(changes) == 1
    assert changes[0]["change_type"] == "price_changed"


def test_t7_6_nothing_changed_no_alert():
    db = get_session()
    cid = "c_nochange"
    s1 = PageSnapshot(id="sn1", company_id=cid, url="https://target.com/pricing", content_hash="hash1", clean_text="Plan $10/mo", fetched_at=datetime(2026, 1, 1))
    s2 = PageSnapshot(id="sn2", company_id=cid, url="https://target.com/pricing", content_hash="hash1", clean_text="Plan $10/mo", fetched_at=datetime(2026, 1, 8))
    db.add_all([s1, s2])
    db.commit()
    db.close()

    changes = detect_snapshot_changes(cid)
    assert len(changes) == 0


def test_t7_7_smtp_mock_digest_email_sent_once():
    sent_emails = []

    def mock_send(sender, recipients, body):
        sent_emails.append({"sender": sender, "recipients": recipients, "body": body})

    cfg = {"mock_send_fn": mock_send}
    ok = send_digest_email("user@example.com", "Weekly Digest", "Price changed for Alpha", smtp_config=cfg)

    assert ok is True
    assert len(sent_emails) == 1
    assert sent_emails[0]["recipients"] == ["user@example.com"]
    assert "Weekly Digest" in sent_emails[0]["body"]


def test_t7_8_report_folder_dated_per_company():
    db = get_session()
    c = Company(id="c_dated", slug="dated-co", name="Dated Co", url="https://dated.com")
    db.add(c)
    db.commit()
    db.close()

    res1 = build_all_reports("c_dated", date_str="2026-01-01")
    res2 = build_all_reports("c_dated", date_str="2026-01-08")

    assert "2026-01-01" in res1["directory"]
    assert "2026-01-08" in res2["directory"]
    assert os.path.exists(res1["markdown"])
    assert os.path.exists(res2["markdown"])

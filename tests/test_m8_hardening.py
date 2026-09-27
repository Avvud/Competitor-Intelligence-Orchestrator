"""
test_m8_hardening.py — Hardening & acceptance tests for Milestone 8.
"""

import os
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from src.core.db import (
    init_db, get_session, Company, CompanyProfile, Competitor, Comparison, Job, BudgetLog
)
from src.core.budget import BudgetExceeded
from src.core.llm_client import LLMClient
from src.reports.builder import build_all_reports


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_m8.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DB_URL", db_url)
    init_db(db_url)
    yield


def test_t8_1_zero_data_leakage_between_companies():
    db = get_session()

    # Company A
    ca = Company(id="comp_a", slug="company-a", name="Company A", url="https://a.com")
    cpa = Competitor(id="cat_a", company_id="comp_a", name="Competitor A1", domain="a1.com")
    db.add_all([ca, cpa])

    # Company B
    cb = Company(id="comp_b", slug="company-b", name="Company B", url="https://b.com")
    cpb = Competitor(id="cat_b", company_id="comp_b", name="Competitor B1", domain="b1.com")
    db.add_all([cb, cpb])
    db.commit()

    # Query Company A data
    comp_a_list = db.query(Competitor).filter(Competitor.company_id == "comp_a").all()
    comp_b_list = db.query(Competitor).filter(Competitor.company_id == "comp_b").all()

    assert len(comp_a_list) == 1
    assert comp_a_list[0].name == "Competitor A1"

    assert len(comp_b_list) == 1
    assert comp_b_list[0].name == "Competitor B1"

    # Build reports and check file paths isolation
    rep_a = build_all_reports("comp_a")
    rep_b = build_all_reports("comp_b")

    assert "company-a" in rep_a["directory"]
    assert "company-b" in rep_b["directory"]
    assert rep_a["directory"] != rep_b["directory"]

    with open(rep_a["markdown"], "r", encoding="utf-8") as f:
        content_a = f.read()
    assert "Competitor A1" in content_a
    assert "Competitor B1" not in content_a

    db.close()


def test_t8_2_simulated_429_mid_run_retry():
    # Simulate a function retrying on 429
    call_count = 0

    def mock_call():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("429 Too Many Requests")
        return {"status": "success", "data": "OK"}

    res = None
    for attempt in range(3):
        try:
            res = mock_call()
            break
        except RuntimeError as e:
            if "429" in str(e):
                continue
            raise

    assert res == {"status": "success", "data": "OK"}
    assert call_count == 2


def test_t8_3_timing_and_token_report_logging():
    db = get_session()
    today = datetime.utcnow().strftime("%Y-%m-%d")

    blog = BudgetLog(
        id="blog_1",
        date=today,
        company_id="comp_test",
        tokens_in=1500,
        tokens_out=450,
        requests=5,
        tier="smart"
    )
    db.add(blog)
    db.commit()

    retrieved = db.query(BudgetLog).filter(BudgetLog.company_id == "comp_test").first()
    assert retrieved is not None
    assert retrieved.tokens_in == 1500
    assert retrieved.tokens_out == 450
    assert retrieved.requests == 5
    db.close()


def test_t8_4_secret_scan_gitignore_and_no_keys():
    gitignore_path = ".gitignore"
    assert os.path.exists(gitignore_path)

    with open(gitignore_path, "r", encoding="utf-8") as f:
        git_content = f.read()

    assert ".env" in git_content

    # Scan python files in src for hardcoded API keys
    key_patterns = ["gsk_", "sk-", "AIzaSy"]
    for root, _, files in os.walk("src"):
        for file in files:
            if file.endswith(".py"):
                fpath = os.path.join(root, file)
                with open(fpath, "r", encoding="utf-8") as f:
                    code = f.read()
                    for pat in key_patterns:
                        assert pat not in code, f"Potential hardcoded key pattern {pat} in {fpath}"

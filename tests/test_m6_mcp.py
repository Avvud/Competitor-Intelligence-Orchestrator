"""
test_m6_mcp.py — Unit tests for Milestone 6 (MCP server and tools).
"""
import pytest
import asyncio
from datetime import datetime
from src.core.db import init_db, get_session, Company, CompanyProfile, Job, BudgetLog
from src.mcp.server import create_mcp_server


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_m6.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DB_URL", db_url)
    init_db(db_url)
    yield


@pytest.mark.asyncio
async def test_t6_1_mcp_handshake_and_tool_listing():
    mcp = create_mcp_server()
    tools = await mcp.list_tools()
    assert len(tools) == 12
    tool_names = {t.name for t in tools}
    expected_tools = {
        "create_company_from_url",
        "confirm_profile",
        "discover_competitors",
        "score_and_rank",
        "collect_data",
        "analyse",
        "generate_comparison",
        "build_reports",
        "detect_changes",
        "get_status",
        "get_budget",
        "list_companies"
    }
    assert tool_names == expected_tools
    for t in tools:
        assert t.description is not None and len(t.description) > 0


@pytest.mark.asyncio
async def test_t6_2_create_company_from_url():
    mcp = create_mcp_server()
    res = await mcp.call_tool("create_company_from_url", {"url": "https://example.com", "name": "Example Corp"})
    data = res.structured_content
    assert "company_id" in data
    assert data["status"] == "pending_profile"
    assert "slug" in data


@pytest.mark.asyncio
async def test_t6_3_discover_competitors_before_confirmation():
    mcp = create_mcp_server()
    res1 = await mcp.call_tool("create_company_from_url", {"url": "https://example.com"})
    cid = res1.structured_content["company_id"]

    res2 = await mcp.call_tool("discover_competitors", {"company_id": cid})
    data = res2.structured_content
    assert data.get("needs_confirmation") is True
    assert "required_action" in data
    assert data["required_action"] == "confirm_profile"


@pytest.mark.asyncio
async def test_t6_4_job_progress_and_status():
    mcp = create_mcp_server()
    db = get_session()
    c = Company(id="c1", slug="comp", name="Comp", url="http://comp.com", status="confirmed")
    cp = CompanyProfile(id="p1", company_id="c1", name="Comp", confirmed_at=datetime.utcnow())
    db.add(c)
    db.add(cp)
    db.commit()
    db.close()

    res = await mcp.call_tool("discover_competitors", {"company_id": "c1"})
    data = res.structured_content
    assert "job_id" in data
    jid = data["job_id"]

    status_res = await mcp.call_tool("get_status", {"company_id": "c1", "job_id": jid})
    sdata = status_res.structured_content
    assert sdata["job_id"] == jid
    assert sdata["status"] == "pending"


@pytest.mark.asyncio
async def test_t6_5_get_budget():
    mcp = create_mcp_server()
    res = await mcp.call_tool("get_budget", {})
    data = res.structured_content
    assert "used" in data
    assert "remaining" in data
    assert "limits" in data
    assert data["used"]["total_requests"] == 0


@pytest.mark.asyncio
async def test_t6_6_tool_output_size():
    mcp = create_mcp_server()
    tools = await mcp.list_tools()
    for t in tools:
        args = {}
        if t.name in ["create_company_from_url"]:
            args = {"url": "https://test.com"}
        elif t.name in [
            "confirm_profile", "discover_competitors", "score_and_rank",
            "collect_data", "analyse", "generate_comparison",
            "build_reports", "detect_changes", "get_status"
        ]:
            args = {"company_id": "dummy_id"}

        res = await mcp.call_tool(t.name, args)
        text_output = res.content[0].text if res.content else ""
        assert len(text_output) < 8000

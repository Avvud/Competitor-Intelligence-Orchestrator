"""
test_m5_comparison.py — M5 test suite T5.1 to T5.8.

All LLM calls are mocked.
No real network calls.
"""
import json
import pytest
import respx
import httpx

from src.core.db import Competitor, ConnectorRecord, AnalysisResult, Comparison, CompanyProfile
from src.core.llm_client import LLMClient
from src.core.models import ComparisonOutput, RollupOutput, RollupItem, Advantage, ThreatOpportunity
from src.analysis.comparison import generate_1v1_comparison
from src.analysis.rollup import generate_tier_rollup, sort_actions
from src.reports.markdown import render_comparison_markdown, render_rollup_markdown
from tests.conftest import groq_ok_response


def mock_groq(respx_mock, data: dict):
    content = json.dumps(data)
    return respx_mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=groq_ok_response(content, model="llama-3.3-70b-versatile"))
    )


def mock_ollama(respx_mock, data: dict):
    content = json.dumps(data)
    return respx_mock.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=groq_ok_response(content, model="gemma3:4b"))
    )


# ---------------------------------------------------------------------------
# T5.1: Output validated by Pydantic model ComparisonOutput
# ---------------------------------------------------------------------------
def test_t5_1_pydantic_output_validation(respx_mock, db_session, fake_env):
    # Setup Company & Competitor in DB
    comp = Competitor(
        id="comp_b1", company_id="c_100", name="Beta Corp", domain="betacorp.com",
        tier="regional", score=75, website="https://betacorp.com"
    )
    db_session.add(comp)
    db_session.commit()

    llm_payload = {
        "company_id": "c_100",
        "competitor": "Beta Corp",
        "tier": "regional",
        "threat_score": 75,
        "dimensions": [
            {
                "name": "Pricing",
                "us": "$10/mo",
                "them": "$15/mo",
                "verdict": "us",
                "sources": []
            }
        ],
        "our_disadvantages": [],
        "their_advantages_to_adopt": [
            {
                "point": "Mobile App UI",
                "effort": "low",
                "impact": "high",
                "sources": []
            }
        ],
        "threats": [],
        "opportunities": [],
        "other_important_points": [],
        "data_gaps": []
    }
    mock_groq(respx_mock, llm_payload)

    llm_client = LLMClient(db_session)
    res = generate_1v1_comparison(db_session, llm_client, "c_100", "comp_b1")

    assert isinstance(res, ComparisonOutput)
    assert res.company_id == "c_100"
    assert res.competitor == "Beta Corp"
    assert res.threat_score == 75
    assert len(res.dimensions) == 1
    assert res.dimensions[0].verdict == "us"


# ---------------------------------------------------------------------------
# T5.2: Claim with no stored source -> removed or moved to data_gaps
# ---------------------------------------------------------------------------
def test_t5_2_unsupported_source_moved_to_data_gaps(respx_mock, db_session, fake_env):
    comp = Competitor(
        id="comp_b2", company_id="c_200", name="Gamma Inc", domain="gammainc.com",
        tier="national", score=80, website="https://gammainc.com"
    )
    # Add one stored source URL to connector records
    rec = ConnectorRecord(
        id="rec_1", company_id="c_200", competitor_id="comp_b2",
        connector="website", source_url="https://gammainc.com/pricing", status="ok"
    )
    db_session.add_all([comp, rec])
    db_session.commit()

    llm_payload = {
        "company_id": "c_200",
        "competitor": "Gamma Inc",
        "tier": "national",
        "threat_score": 80,
        "dimensions": [],
        "our_disadvantages": [
            {
                "point": "Slower API response",
                "evidence": "Benchmark tests show 200ms latency",
                "sources": ["https://fake-hallucinated-source.com/benchmarks"]  # NOT in stored sources
            }
        ],
        "their_advantages_to_adopt": [],
        "threats": [],
        "opportunities": [],
        "other_important_points": [],
        "data_gaps": []
    }
    mock_groq(respx_mock, llm_payload)

    llm_client = LLMClient(db_session)
    res = generate_1v1_comparison(db_session, llm_client, "c_200", "comp_b2")

    # The unverified disadvantage should be dropped from our_disadvantages and logged in data_gaps
    assert len(res.our_disadvantages) == 0
    assert any("Slower API response" in gap for gap in res.data_gaps)


# ---------------------------------------------------------------------------
# T5.3: No threats found -> section present with "None found"
# ---------------------------------------------------------------------------
def test_t5_3_no_threats_found_section_present(respx_mock, db_session, fake_env):
    comp = Competitor(
        id="comp_b3", company_id="c_300", name="Delta LLC", domain="deltallc.com",
        tier="state", score=40, website="https://deltallc.com"
    )
    db_session.add(comp)
    db_session.commit()

    llm_payload = {
        "company_id": "c_300",
        "competitor": "Delta LLC",
        "tier": "state",
        "threat_score": 40,
        "dimensions": [],
        "our_disadvantages": [],
        "their_advantages_to_adopt": [],
        "threats": [],  # Empty threats list from LLM
        "opportunities": [],
        "other_important_points": [],
        "data_gaps": []
    }
    mock_groq(respx_mock, llm_payload)

    llm_client = LLMClient(db_session)
    res = generate_1v1_comparison(db_session, llm_client, "c_300", "comp_b3")

    # Verify threats section is present with "None found"
    assert len(res.threats) == 1
    assert res.threats[0].point == "None found"


# ---------------------------------------------------------------------------
# T5.4: Each adoptable advantage has effort and impact rating
# ---------------------------------------------------------------------------
def test_t5_4_adoptable_advantage_effort_impact(respx_mock, db_session, fake_env):
    comp = Competitor(
        id="comp_b4", company_id="c_400", name="Epsilon Software", domain="epsilon.com",
        tier="regional", score=60, website="https://epsilon.com"
    )
    db_session.add(comp)
    db_session.commit()

    llm_payload = {
        "company_id": "c_400",
        "competitor": "Epsilon Software",
        "tier": "regional",
        "threat_score": 60,
        "dimensions": [],
        "our_disadvantages": [],
        "their_advantages_to_adopt": [
            {
                "point": "One-click OAuth Integration",
                "effort": "low",
                "impact": "high",
                "sources": []
            },
            {
                "point": "Custom Theme Builder",
                "effort": "high",
                "impact": "med",
                "sources": []
            }
        ],
        "threats": [],
        "opportunities": [],
        "other_important_points": [],
        "data_gaps": []
    }
    mock_groq(respx_mock, llm_payload)

    llm_client = LLMClient(db_session)
    res = generate_1v1_comparison(db_session, llm_client, "c_400", "comp_b4")

    assert len(res.their_advantages_to_adopt) == 2
    assert res.their_advantages_to_adopt[0].effort in ("low", "med", "high")
    assert res.their_advantages_to_adopt[0].impact in ("low", "med", "high")
    assert res.their_advantages_to_adopt[1].effort == "high"
    assert res.their_advantages_to_adopt[1].impact == "med"


# ---------------------------------------------------------------------------
# T5.5: Groq down/budget -> local fallback used & report flagged lower_quality
# ---------------------------------------------------------------------------
def test_t5_5_groq_fallback_lower_quality_flag(respx_mock, db_session, fake_env):
    comp = Competitor(
        id="comp_b5", company_id="c_500", name="Zeta Tech", domain="zetatech.com",
        tier="national", score=90, website="https://zetatech.com"
    )
    db_session.add(comp)
    db_session.commit()

    # Fail Groq call (HTTP 500 or timeout)
    respx_mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    # Mock fallback to Ollama or fast cloud
    fallback_payload = {
        "company_id": "c_500",
        "competitor": "Zeta Tech",
        "tier": "national",
        "threat_score": 90,
        "dimensions": [],
        "our_disadvantages": [],
        "their_advantages_to_adopt": [],
        "threats": [],
        "opportunities": [],
        "other_important_points": [],
        "data_gaps": []
    }
    mock_ollama(respx_mock, fallback_payload)

    llm_client = LLMClient(db_session)
    res = generate_1v1_comparison(db_session, llm_client, "c_500", "comp_b5")

    # Lower quality flag must be True due to Groq failure fallback
    assert res.lower_quality is True


# ---------------------------------------------------------------------------
# T5.6: Roll-up actions sorted by impact desc then effort asc
# ---------------------------------------------------------------------------
def test_t5_6_rollup_actions_sorted():
    items = [
        RollupItem(point="Task 1", competitor="Comp A", effort="high", impact="low"),
        RollupItem(point="Task 2", competitor="Comp B", effort="low", impact="high"),
        RollupItem(point="Task 3", competitor="Comp C", effort="high", impact="high"),
        RollupItem(point="Task 4", competitor="Comp D", effort="med", impact="med"),
    ]

    sorted_items = sort_actions(items)

    # Expected order:
    # 1. Task 2 (impact=high, effort=low)
    # 2. Task 3 (impact=high, effort=high)
    # 3. Task 4 (impact=med, effort=med)
    # 4. Task 1 (impact=low, effort=high)
    assert sorted_items[0].point == "Task 2"
    assert sorted_items[1].point == "Task 3"
    assert sorted_items[2].point == "Task 4"
    assert sorted_items[3].point == "Task 1"


# ---------------------------------------------------------------------------
# T5.7: Markdown render matches golden file/structure
# ---------------------------------------------------------------------------
def test_t5_7_markdown_rendering():
    comp_output = ComparisonOutput(
        company_id="c_700",
        competitor="Golden Competitor",
        tier="regional",
        threat_score=85,
        dimensions=[],
        our_disadvantages=[],
        their_advantages_to_adopt=[
            Advantage(point="Dark Mode", effort="low", impact="high", sources=[])
        ],
        threats=[
            ThreatOpportunity(point="None found", evidence="Not available", sources=[])
        ],
        opportunities=[],
        other_important_points=[],
        data_gaps=[]
    )

    md = render_comparison_markdown(comp_output)

    assert "# Competitive Comparison: Our Company vs Golden Competitor" in md
    assert "Dark Mode" in md
    assert "Impact: `HIGH`, Effort: `LOW`" in md
    assert "## Threats" in md
    assert "None found" in md


# ---------------------------------------------------------------------------
# T5.8: Two companies isolation: no competitor from Co A in Co B comparison
# ---------------------------------------------------------------------------
def test_t5_8_company_isolation_in_comparisons(respx_mock, db_session, fake_env):
    # Company A setup
    comp_a = Competitor(id="comp_co_a", company_id="co_A", name="Co A Rival", tier="regional")
    # Company B setup
    comp_b = Competitor(id="comp_co_b", company_id="co_B", name="Co B Rival", tier="regional")
    
    db_session.add_all([comp_a, comp_b])
    db_session.commit()

    llm_payload_b = {
        "company_id": "co_B",
        "competitor": "Co B Rival",
        "tier": "regional",
        "threat_score": 50,
        "dimensions": [],
        "our_disadvantages": [],
        "their_advantages_to_adopt": [],
        "threats": [],
        "opportunities": [],
        "other_important_points": [],
        "data_gaps": []
    }
    mock_groq(respx_mock, llm_payload_b)

    llm_client = LLMClient(db_session)
    res_b = generate_1v1_comparison(db_session, llm_client, "co_B", "comp_co_b")

    # Verify Co B's comparison only references Co B Rival and company_id co_B
    assert res_b.company_id == "co_B"
    assert res_b.competitor == "Co B Rival"
    assert "Co A Rival" not in res_b.model_dump_json()

    # Query DB comparisons for co_B
    db_comps_b = db_session.query(Comparison).filter(Comparison.company_id == "co_B").all()
    assert len(db_comps_b) == 1
    assert db_comps_b[0].competitor_id == "comp_co_b"

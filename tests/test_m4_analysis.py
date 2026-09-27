"""
test_m4_analysis.py — Tests T4.1 through T4.7 for M4 analysis module.

All HTTP calls are mocked with respx.
No real API calls or local LLMs needed.
"""
import json
import pytest
import respx
import httpx

from src.analysis.chunker import chunk_text, estimate_tokens
from src.analysis.validator import filter_unsupported_fields, sanitize_pricing_data
from src.analysis.pricing import extract_pricing
from src.analysis.reviews import cluster_reviews
from src.analysis.features import extract_feature_matrix
from src.analysis.applicability import apply_industry_applicability
from src.analysis.pipeline import run_analysis
from src.core.llm_client import LLMClient
from tests.conftest import groq_ok_response


def ollama_json_response(data: dict) -> httpx.Response:
    content = json.dumps(data)
    return httpx.Response(200, json=groq_ok_response(content, model="gemma3:4b"))


def mock_ollama(respx_mock, data: dict):
    return respx_mock.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=ollama_json_response(data)
    )


# ---------------------------------------------------------------------------
# T4.1: Pricing tier extraction & default missing fields
# ---------------------------------------------------------------------------
def test_t4_1_pricing_extraction(respx_mock, db_session, fake_env):
    llm_output = {
        "tiers": [
            {"name": "Starter", "price": "$10/mo", "features": ["Feature A"]},
            {"name": "Pro", "price": "$50/mo"}  # missing features & limits
        ],
        "currency": "USD"
    }
    mock_ollama(respx_mock, llm_output)

    llm_client = LLMClient(db_session)
    pricing_text = "Starter plan is $10/mo with Feature A. Pro plan is $50/mo."

    result = extract_pricing(llm_client, pricing_text)

    assert "tiers" in result
    assert len(result["tiers"]) == 2
    assert result["tiers"][0]["name"] == "Starter"
    assert result["tiers"][0]["price"] == "$10/mo"
    # Pro tier missing limits & features should default to "Not available" / []
    assert result["tiers"][1]["name"] == "Pro"
    assert result["tiers"][1]["limits"] == "Not available"
    assert result["tiers"][1]["features"] == []
    assert result.get("free_trial") == "Not available"


# ---------------------------------------------------------------------------
# T4.2: 20 fixture reviews clustering with counts and review_ids
# ---------------------------------------------------------------------------
def test_t4_2_reviews_clustering(respx_mock, db_session, fake_env):
    reviews_input = [
        {"id": f"rev_{i}", "text": f"Review content number {i} about speed and usability."}
        for i in range(1, 21)
    ]

    llm_output = {
        "themes": [
            {
                "theme": "Speed & Performance",
                "sentiment": "positive",
                "count": 12,
                "review_ids": [f"rev_{i}" for i in range(1, 13)],
                "summary": "Great overall speed."
            },
            {
                "theme": "Usability",
                "sentiment": "positive",
                "count": 8,
                "review_ids": [f"rev_{i}" for i in range(13, 21)],
                "summary": "Very easy to use."
            }
        ]
    }
    mock_ollama(respx_mock, llm_output)

    llm_client = LLMClient(db_session)
    result = cluster_reviews(llm_client, reviews_input)

    assert result["total_reviews"] == 20
    assert len(result["themes"]) == 2
    assert result["themes"][0]["count"] == 12
    assert len(result["themes"][0]["review_ids"]) == 12
    assert result["themes"][1]["count"] == 8
    assert len(result["themes"][1]["review_ids"]) == 8

    all_ids = set()
    for t in result["themes"]:
        all_ids.update(t["review_ids"])
    assert len(all_ids) == 20


# ---------------------------------------------------------------------------
# T4.3: Text over 1500 tokens split correctly with no text lost
# ---------------------------------------------------------------------------
def test_t4_3_chunking_large_text():
    # Build text of ~3000 tokens (approx 12,000 chars / 2000 words)
    paragraphs = []
    for p_idx in range(30):
        paragraphs.append(f"Paragraph {p_idx}: " + " ".join([f"word_{p_idx}_{w}" for w in range(60)]))
    full_text = "\n\n".join(paragraphs)

    chunks = chunk_text(full_text, max_tokens=1500)

    assert len(chunks) > 1

    # Verify no chunk exceeds 1500 tokens limit (1500 tokens ~ 6000 chars)
    for c in chunks:
        assert estimate_tokens(c) <= 1500

    # Verify no text lost: all original words are present in joined chunks
    reconstructed = "\n\n".join(chunks)
    for p in paragraphs:
        assert p in reconstructed


# ---------------------------------------------------------------------------
# T4.4: Unchanged page result returned with ZERO LLM calls (hash caching)
# ---------------------------------------------------------------------------
def test_t4_4_unchanged_page_zero_llm_calls(respx_mock, db_session, fake_env):
    llm_output = {
        "tiers": [{"name": "Basic", "price": "$5/mo", "features": [], "limits": "None"}],
        "currency": "USD"
    }
    route = mock_ollama(respx_mock, llm_output)

    llm_client = LLMClient(db_session)
    page_text = "Basic tier costs $5/mo."

    # First run -> triggers LLM call
    res1 = run_analysis(
        db=db_session,
        llm_client=llm_client,
        company_id="comp_123",
        analysis_type="pricing",
        text=page_text,
        competitor_id="comp_id_456"
    )
    assert route.call_count == 1
    assert res1["tiers"][0]["name"] == "Basic"

    # Second run with EXACT SAME page_text -> should return cached without LLM call
    res2 = run_analysis(
        db=db_session,
        llm_client=llm_client,
        company_id="comp_123",
        analysis_type="pricing",
        text=page_text,
        competitor_id="comp_id_456"
    )
    assert route.call_count == 1  # Still 1! Zero additional calls made.
    assert res2["tiers"][0]["name"] == "Basic"


# ---------------------------------------------------------------------------
# T4.5: Model returns field not in schema/context -> validator drops it
# ---------------------------------------------------------------------------
def test_t4_5_drop_unsupported_fields():
    raw_data = {
        "sso_saml_integration": "Available",
        "mobile_app": "Available",
        "hallucinated_unsupported_key": "Should be dropped"
    }
    valid_keys = {"sso_saml_integration", "mobile_app"}

    cleaned, unsupported = filter_unsupported_fields(raw_data, valid_keys)

    assert "hallucinated_unsupported_key" not in cleaned
    assert "hallucinated_unsupported_key" in unsupported
    assert cleaned["sso_saml_integration"] == "Available"
    assert cleaned["mobile_app"] == "Available"


# ---------------------------------------------------------------------------
# T4.6: Non-applicable dimension marked 'not_applicable' instead of null
# ---------------------------------------------------------------------------
def test_t4_6_industry_non_applicable_dimensions():
    feature_matrix = {
        "sso_saml_integration": "Available",
        "api_rate_limits": "1000/min"
    }

    # For software_saas, physical_locations & in_store_pickup are non-applicable
    result = apply_industry_applicability(feature_matrix, "software_saas")

    assert result["physical_locations"] == "not_applicable"
    assert result["in_store_pickup"] == "not_applicable"
    assert result["sso_saml_integration"] == "Available"


# ---------------------------------------------------------------------------
# T4.7: Different industries flag different dimensions as not_applicable
# ---------------------------------------------------------------------------
def test_t4_7_different_industries_different_applicability():
    feature_matrix = {}

    saas_res = apply_industry_applicability(feature_matrix, "software_saas")
    retail_res = apply_industry_applicability(feature_matrix, "retail_e_commerce")

    # SaaS has physical_locations non-applicable
    assert saas_res.get("physical_locations") == "not_applicable"
    assert saas_res.get("sso_saml_integration") != "not_applicable"

    # Retail has sso_saml_integration non-applicable
    assert retail_res.get("sso_saml_integration") == "not_applicable"
    assert retail_res.get("physical_locations") != "not_applicable"

"""
features.py — Feature matrix extraction and industry applicability mapping.
"""
import json
from typing import Any
from src.core.llm_client import LLMClient
from src.analysis.applicability import apply_industry_applicability

FEATURE_EXTRACT_PROMPT = """You are a software product analyst.
Extract product features and capabilities mentioned in the text below into a structured JSON map.

Text:
{text}

Return a single JSON object mapping dimension/feature names to string values or boolean/status strings:
{{
  "sso_saml_integration": "Available" or "not_supported",
  "api_rate_limits": "1000 req/min" or "Not specified",
  "mobile_app": "iOS and Android" or "not_supported",
  "physical_locations": "Not specified",
  "in_store_pickup": "Not specified"
}}
"""


def extract_feature_matrix(
    llm_client: LLMClient,
    text: str,
    industry: str = ""
) -> dict[str, Any]:
    """
    Extract feature matrix from text and apply industry applicability rules.
    """
    prompt = FEATURE_EXTRACT_PROMPT.format(text=text[:6000])

    result = llm_client.call(
        task_type="extract_features",
        prompt=prompt,
        expect_json=True
    )

    try:
        data = json.loads(result.content)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    if industry:
        data = apply_industry_applicability(data, industry)

    return data

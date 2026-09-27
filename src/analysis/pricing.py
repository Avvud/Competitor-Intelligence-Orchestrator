"""
pricing.py — Extracts pricing plans, tiers, and features from raw page content.
Defaults missing fields to "Not available".
"""
import json
from typing import Any
from src.core.llm_client import LLMClient
from src.analysis.validator import sanitize_pricing_data

PRICING_PROMPT = """You are a precise data extractor. Extract all pricing information from the text below.
Return a single JSON object with this exact structure:
{{
  "tiers": [
    {{
      "name": "tier name or 'Not available'",
      "price": "price per month/year or 'Not available'",
      "features": ["feature 1", "feature 2"],
      "limits": "limit details or 'Not available'"
    }}
  ],
  "currency": "e.g. USD, EUR or 'Not available'",
  "billing_cycles": ["monthly", "annual"],
  "free_trial": "e.g. 14 days or 'Not available'"
}}

If any field or feature detail is missing or not mentioned, set its value to "Not available".

Text:
{text}
"""


def extract_pricing(llm_client: LLMClient, text: str) -> dict[str, Any]:
    """
    Extract pricing structure from text using BULK LLM tier.
    Returns cleaned dict with missing fields defaulted to "Not available".
    """
    prompt = PRICING_PROMPT.format(text=text[:6000])  # token safety
    
    result = llm_client.call(
        task_type="extract_pricing",
        prompt=prompt,
        expect_json=True
    )

    try:
        data = json.loads(result.content)
        if not isinstance(data, dict):
            data = {"tiers": []}
    except Exception:
        data = {"tiers": []}

    cleaned, _ = sanitize_pricing_data(data)
    return cleaned

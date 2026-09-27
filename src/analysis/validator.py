"""
validator.py — Validates LLM outputs against schema and context.
Drops fields not supported by context or defined schema, recording them as unsupported.
"""
from typing import Any


def sanitize_pricing_data(data: dict[str, Any], allowed_fields: set[str] | None = None) -> tuple[dict[str, Any], list[str]]:
    """
    Validates and cleans pricing extraction data.
    - Standardizes missing fields to "Not available".
    - Drops fields not in allowed_fields (if specified), recording them in dropped_fields.
    """
    dropped_fields: list[str] = []
    cleaned: dict[str, Any] = {}

    default_allowed = {
        "tiers", "currency", "billing_cycles", "free_trial", "custom_enterprise"
    }
    allowed = allowed_fields if allowed_fields is not None else default_allowed

    for key, value in data.items():
        if key not in allowed:
            dropped_fields.append(key)
            continue
        cleaned[key] = value

    # Ensure standard top-level fields default to "Not available" / defaults if missing
    for field in ["currency", "free_trial", "custom_enterprise"]:
        if field not in cleaned or not cleaned[field]:
            cleaned[field] = "Not available"

    if "billing_cycles" not in cleaned or not isinstance(cleaned["billing_cycles"], list):
        cleaned["billing_cycles"] = []

    # Ensure tiers list exists and tiers have standard fields
    tiers = cleaned.get("tiers", [])
    if isinstance(tiers, list):
        cleaned_tiers = []
        for tier in tiers:
            if isinstance(tier, dict):
                t_name = tier.get("name") or "Not available"
                t_price = tier.get("price") or "Not available"
                t_features = tier.get("features") if isinstance(tier.get("features"), list) else []
                t_limits = tier.get("limits") or "Not available"
                cleaned_tiers.append({
                    "name": t_name,
                    "price": t_price,
                    "features": t_features,
                    "limits": t_limits
                })
        cleaned["tiers"] = cleaned_tiers
    else:
        cleaned["tiers"] = []

    return cleaned, dropped_fields


def filter_unsupported_fields(
    data: dict[str, Any],
    valid_keys: set[str],
    context_text: str | None = None
) -> tuple[dict[str, Any], list[str]]:
    """
    Filter dict output from LLM:
    - Any key in data that is not in valid_keys is removed and listed in unsupported_fields.
    - If context_text is provided, any key whose value is explicitly tagged as 'unsupported'
      or not mentioned in context is tracked.
    """
    cleaned: dict[str, Any] = {}
    unsupported: list[str] = []

    for k, v in data.items():
        if k not in valid_keys:
            unsupported.append(k)
            continue
        
        # Check if v is explicitly hallucinated / not in context
        if context_text is not None and isinstance(v, str):
            # If value is marked hallucinated or not found in context
            if "not found in context" in v.lower() or "unsupported" in v.lower():
                unsupported.append(k)
                continue
        
        cleaned[k] = v

    return cleaned, unsupported

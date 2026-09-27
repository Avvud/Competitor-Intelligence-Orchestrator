"""
router.py — maps task types to LLM tiers.

SMART = Groq cloud (good reasoning needed)
BULK  = local Ollama Gemma (extraction, classification, summarisation)
"""

# Simple lookup table — no classes needed
TASK_TIER: dict[str, str] = {
    # ---- BULK tier (local Gemma) ----
    "extract_profile":    "bulk",
    "classify_industry":  "bulk",
    "extract_pricing":    "bulk",
    "extract_features":   "bulk",
    "summarise_review":   "bulk",
    "sentiment_chunk":    "bulk",
    "cluster_reviews":    "bulk",

    # ---- SMART tier (Groq) ----
    "plan_discovery_queries": "smart",
    "fuzzy_dedupe":           "smart",
    "score_overlap":          "smart",
    "gap_analysis":           "smart",
    "write_comparison":       "smart",
    "write_rollup":           "smart",
}


def route(task_type: str) -> str:
    """Return 'smart' or 'bulk' for the given task type."""
    tier = TASK_TIER.get(task_type)
    if tier is None:
        raise ValueError(f"Unknown task type: {task_type!r}. "
                         f"Known types: {list(TASK_TIER)}")
    return tier

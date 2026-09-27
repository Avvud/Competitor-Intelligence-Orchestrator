"""
reviews.py — Clusters review texts into sentiment themes with counts and review_ids.
"""
import json
from typing import Any
from src.core.llm_client import LLMClient

REVIEWS_CLUSTER_PROMPT = """You are a customer feedback analyst.
Analyze the following list of customer reviews and group them into distinct feedback themes (e.g., 'Ease of Use', 'Customer Support', 'Pricing', 'Performance').

Reviews:
{reviews_json}

Return a single JSON object with this exact structure:
{{
  "themes": [
    {{
      "theme": "Theme Name",
      "sentiment": "positive" or "negative" or "mixed",
      "count": integer_count_of_reviews,
      "review_ids": ["review_id_1", "review_id_2"],
      "summary": "Brief explanation of feedback in this theme"
    }}
  ]
}}
"""


def cluster_reviews(llm_client: LLMClient, reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Cluster list of reviews (dict with 'id'/'review_id' and 'text') into themes.
    Ensures theme counts match review_ids length.
    """
    if not reviews:
        return {"themes": [], "total_reviews": 0}

    # Standardize review representations for prompt
    formatted_reviews = []
    all_ids = set()
    for i, r in enumerate(reviews):
        r_id = str(r.get("id") or r.get("review_id") or f"rev_{i+1}")
        r_text = str(r.get("text") or r.get("content") or "")
        formatted_reviews.append({"id": r_id, "text": r_text})
        all_ids.add(r_id)

    prompt = REVIEWS_CLUSTER_PROMPT.format(
        reviews_json=json.dumps(formatted_reviews, indent=2)
    )

    result = llm_client.call(
        task_type="cluster_reviews",
        prompt=prompt,
        expect_json=True
    )

    try:
        data = json.loads(result.content)
        if not isinstance(data, dict):
            data = {"themes": []}
    except Exception:
        data = {"themes": []}

    raw_themes = data.get("themes", [])
    if not isinstance(raw_themes, list):
        raw_themes = []

    cleaned_themes = []
    assigned_ids = set()

    for item in raw_themes:
        if not isinstance(item, dict):
            continue
        theme_name = str(item.get("theme", "General Feedback"))
        sentiment = str(item.get("sentiment", "neutral")).lower()
        if sentiment not in ("positive", "negative", "mixed", "neutral"):
            sentiment = "neutral"
            
        r_ids = item.get("review_ids", [])
        if not isinstance(r_ids, list):
            r_ids = []
        r_ids = [str(rid) for rid in r_ids if str(rid) in all_ids]
        
        assigned_ids.update(r_ids)
        
        summary = str(item.get("summary", ""))

        cleaned_themes.append({
            "theme": theme_name,
            "sentiment": sentiment,
            "count": len(r_ids),
            "review_ids": r_ids,
            "summary": summary
        })

    # If any review was not assigned to a theme, group into "Other"
    unassigned = all_ids - assigned_ids
    if unassigned:
        cleaned_themes.append({
            "theme": "Other",
            "sentiment": "neutral",
            "count": len(unassigned),
            "review_ids": sorted(list(unassigned)),
            "summary": "Miscellaneous review feedback"
        })

    return {
        "themes": cleaned_themes,
        "total_reviews": len(reviews)
    }

"""
scorer.py — score competitor candidates 0-100.

Weights (must sum to 100):
  product_overlap   30   — how many product categories overlap
  customer_overlap  20   — target customer segment match
  price_band        15   — same price band = high similarity
  geography         20   — same city > state > country > different
  size              15   — similar size band

All deterministic sub-scores are calculated without LLM.
The LLM (SMART tier) is used ONLY for fuzzy product/customer overlap
when no structured keywords are available, and only once per pair (cached).

Scores are guaranteed to be in [0, 100] and deterministic for identical inputs.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Weights
WEIGHTS = {
    "product_overlap":  30,
    "customer_overlap": 20,
    "price_band":       15,
    "geography":        20,
    "size":             15,
}
assert sum(WEIGHTS.values()) == 100


# ---------------------------------------------------------------------------
# Sub-scorers (each returns 0.0–1.0)
# ---------------------------------------------------------------------------

# Price band ordering for proximity score
_PRICE_ORDER = ["budget", "mid", "premium", "enterprise"]


def _price_band_score(our: str | None, theirs: str | None) -> float:
    """
    Same = 1.0, adjacent = 0.5, two apart = 0.25, three+ = 0.0.
    Unknown = 0.5 (neutral).
    """
    if not our or not theirs or our == "unknown" or theirs == "unknown":
        return 0.5
    try:
        ia = _PRICE_ORDER.index(our.lower())
        ib = _PRICE_ORDER.index(theirs.lower())
        diff = abs(ia - ib)
        return max(0.0, 1.0 - diff * 0.375)
    except ValueError:
        return 0.5


_SIZE_ORDER = ["micro", "small", "medium", "large"]


def _size_score(our: str | None, theirs: str | None) -> float:
    """Same logic as price band."""
    if not our or not theirs or our == "unknown" or theirs == "unknown":
        return 0.5
    try:
        ia = _SIZE_ORDER.index(our.lower())
        ib = _SIZE_ORDER.index(theirs.lower())
        diff = abs(ia - ib)
        return max(0.0, 1.0 - diff * 0.375)
    except ValueError:
        return 0.5


def _geo_score(
    our_city: str | None, our_state: str | None, our_country: str | None,
    their_city: str | None, their_state: str | None, their_country: str | None,
) -> float:
    """
    City match = 1.0, state match = 0.75, country match = 0.5, different = 0.0.
    Unknown = 0.25 (slight penalty since geography is unknown).
    """
    def clean(s: str | None) -> str | None:
        if not s or s.lower() in ("unknown", "none"):
            return None
        return s.lower().strip()

    oc = clean(our_city)
    os_ = clean(our_state)
    ocountry = clean(our_country)
    tc = clean(their_city)
    ts = clean(their_state)
    tcountry = clean(their_country)

    if oc and tc and oc == tc:
        return 1.0
    if os_ and ts and os_ == ts:
        return 0.75
    if ocountry and tcountry and ocountry == tcountry:
        return 0.5
    if not ocountry or not tcountry:
        return 0.25
    return 0.0


def _keyword_overlap(our_keywords: list[str], their_keywords: list[str]) -> float:
    """Jaccard similarity of keyword sets."""
    a = {k.lower().strip() for k in our_keywords if k.strip()}
    b = {k.lower().strip() for k in their_keywords if k.strip()}
    if not a and not b:
        return 0.5
    if not a or not b:
        return 0.25
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------

def score_competitor(
    our_profile: dict[str, Any],
    candidate: dict[str, Any],
    llm_client=None,
    company_id: str | None = None,
) -> int:
    """
    Score a candidate competitor against our company profile.
    Returns an integer in [0, 100].

    our_profile and candidate are plain dicts with keys matching CompanyProfile fields.
    llm_client is optional; if provided, used for fuzzy overlap when no keywords exist.
    """
    # --- Product overlap ---
    our_kw   = our_profile.get("search_keywords") or []
    their_kw = candidate.get("search_keywords") or []
    if isinstance(our_kw, str):
        try:
            our_kw = json.loads(our_kw)
        except Exception:
            our_kw = []
    if isinstance(their_kw, str):
        try:
            their_kw = json.loads(their_kw)
        except Exception:
            their_kw = []

    product_score = _keyword_overlap(our_kw, their_kw)

    # If no keywords for either side and LLM available, ask it
    if product_score == 0.5 and llm_client and (not our_kw or not their_kw):
        product_score = _llm_fuzzy_overlap(
            our_profile, candidate, "product", llm_client, company_id
        )

    # --- Customer overlap ---
    our_seg   = (our_profile.get("target_customers") or "").lower()
    their_seg = (candidate.get("target_customers") or "").lower()
    if our_seg and their_seg and our_seg not in ("unknown", "none"):
        # Simple token overlap
        our_tokens   = set(our_seg.split())
        their_tokens = set(their_seg.split())
        inter = len(our_tokens & their_tokens)
        union = len(our_tokens | their_tokens)
        customer_score = inter / union if union else 0.5
    else:
        customer_score = 0.5

    # --- Price band ---
    price_score = _price_band_score(
        our_profile.get("price_band"), candidate.get("price_band")
    )

    # --- Geography ---
    geo_score = _geo_score(
        our_profile.get("hq_city"),    our_profile.get("hq_state"),    our_profile.get("hq_country"),
        candidate.get("hq_city"),      candidate.get("hq_state"),      candidate.get("hq_country"),
    )

    # --- Size ---
    size_score = _size_score(
        our_profile.get("size_hint"), candidate.get("size_hint")
    )

    # Weighted sum
    raw = (
        product_score  * WEIGHTS["product_overlap"]
        + customer_score * WEIGHTS["customer_overlap"]
        + price_score    * WEIGHTS["price_band"]
        + geo_score      * WEIGHTS["geography"]
        + size_score     * WEIGHTS["size"]
    )
    return max(0, min(100, round(raw)))


def assign_tier(score: int, geo_score: float) -> str:
    """
    Assign a competitor tier based on score and geography match.

    Returns one of: "direct" | "regional" | "indirect" | "noise"
    """
    if score >= 70:
        return "direct"
    if score >= 50:
        return "regional"
    if score >= 30:
        return "indirect"
    return "noise"


# ---------------------------------------------------------------------------
# LLM fuzzy overlap (SMART tier, cached)
# ---------------------------------------------------------------------------

def _llm_fuzzy_overlap(
    our_profile: dict, candidate: dict, overlap_type: str,
    llm_client, company_id: str | None
) -> float:
    """
    Ask the SMART-tier LLM for a 0.0–1.0 overlap score.
    Falls back to 0.5 on any error (neutral, not penalising).
    """
    our_desc   = our_profile.get("industry_label") or our_profile.get("name") or "unknown business"
    their_desc = candidate.get("industry_label") or candidate.get("name") or "unknown business"

    prompt = (
        f"Rate the {overlap_type} overlap between these two businesses on a scale from 0.0 to 1.0.\n"
        f"Business A: {our_desc}\n"
        f"Business B: {their_desc}\n"
        f"Return ONLY a single decimal number between 0.0 and 1.0. No explanation."
    )
    try:
        result = llm_client.call(
            "score_competitor", prompt, company_id=company_id, expect_json=False
        )
        value = float(result.content.strip())
        return max(0.0, min(1.0, value))
    except Exception as exc:
        logger.debug("LLM fuzzy overlap failed: %s — using 0.5", exc)
        return 0.5

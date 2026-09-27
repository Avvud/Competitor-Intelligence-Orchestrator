"""
models.py — Pydantic schemas used for validation throughout the project.

Keep them simple. If a field is optional, say so.
"""

from typing import Literal
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Company profile (output of M1 profile extraction)
# ---------------------------------------------------------------------------

class CompanyProfile(BaseModel):
    company_id:       str
    name:             str | None = None
    industry_label:   str | None = None
    industry_cat:     str | None = None   # normalised e.g. "food_beverage"
    products:         list[str] = []
    target_customers: str | None = None
    price_band:       str | None = None   # budget | mid | premium | enterprise
    business_model:   str | None = None
    hq_city:          str | None = None
    hq_state:         str | None = None
    hq_country:       str | None = None
    service_areas:    list[str] = []
    social_handles:   dict[str, str] = {}
    size_hint:        str | None = None
    founding_year:    int | None = None
    search_keywords:  list[str] = []
    # Per-field confidence 0.0–1.0
    confidence:       dict[str, float] = {}
    # Source URL for each field
    source_urls:      dict[str, str] = {}
    confirmed:        bool = False


# ---------------------------------------------------------------------------
# Comparison output (section 7 of spec)
# ---------------------------------------------------------------------------

class Dimension(BaseModel):
    name:    str
    us:      str
    them:    str
    verdict: Literal["us", "them", "tie", "unknown", "not_applicable"]
    sources: list[str] = []


class Disadvantage(BaseModel):
    point:    str
    evidence: str
    sources:  list[str] = []


class Advantage(BaseModel):
    point:   str
    effort:  Literal["low", "med", "high"]
    impact:  Literal["low", "med", "high"]
    sources: list[str] = []


class ThreatOpportunity(BaseModel):
    point:    str
    evidence: str = "Not available"
    sources:  list[str] = []


class ComparisonOutput(BaseModel):
    company_id:              str
    competitor:              str
    tier:                    Literal["regional", "state", "national"]
    threat_score:            int = Field(ge=0, le=100)
    dimensions:              list[Dimension] = []
    our_disadvantages:       list[Disadvantage] = []
    their_advantages_to_adopt: list[Advantage] = []
    threats:                 list[ThreatOpportunity] = []
    opportunities:           list[ThreatOpportunity] = []
    other_important_points:  list[ThreatOpportunity] = []
    data_gaps:               list[str] = []
    lower_quality:           bool = False


class RollupItem(BaseModel):
    point:      str
    competitor: str
    effort:     Literal["low", "med", "high"]
    impact:     Literal["low", "med", "high"]
    sources:    list[str] = []


class RollupOutput(BaseModel):
    company_id: str
    tier:       Literal["regional", "state", "national"]
    actions:    list[RollupItem] = []


# ---------------------------------------------------------------------------
# Job progress
# ---------------------------------------------------------------------------

class JobProgress(BaseModel):
    step:    int = 0
    total:   int = 1
    message: str = ""


# ---------------------------------------------------------------------------
# LLM call result (internal)
# ---------------------------------------------------------------------------

class LLMResult(BaseModel):
    content:       str
    model:         str
    tier:          Literal["smart", "bulk"]
    from_cache:    bool = False
    lower_quality: bool = False
    tokens_in:     int = 0
    tokens_out:    int = 0

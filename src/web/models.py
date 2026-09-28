"""
models.py — Pydantic schemas for the REST API.
"""

from typing import Any, List, Optional
from pydantic import BaseModel, Field


class CompanyCreateRequest(BaseModel):
    url: str = Field(..., example="https://stripe.com")
    name: Optional[str] = Field(None, example="Stripe")
    industry: Optional[str] = Field(None, example="Fintech")
    hq_city: Optional[str] = Field(None, example="San Francisco")
    hq_state: Optional[str] = Field(None, example="CA")
    hq_country: Optional[str] = Field(None, example="USA")


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    industry_label: Optional[str] = None
    industry_cat: Optional[str] = None
    products: Optional[List[str]] = None
    target_customers: Optional[str] = None
    price_band: Optional[str] = None
    business_model: Optional[str] = None
    hq_city: Optional[str] = None
    hq_state: Optional[str] = None
    hq_country: Optional[str] = None
    size_hint: Optional[str] = None
    founding_year: Optional[int] = None
    search_keywords: Optional[List[str]] = None


class CompetitorUpdateRequest(BaseModel):
    approved: bool


class CompetitorCreateRequest(BaseModel):
    name: str = Field(..., example="Adyen")
    url: Optional[str] = Field(None, example="https://adyen.com")
    domain: Optional[str] = Field(None, example="adyen.com")
    tier: Optional[str] = Field("direct", example="direct")
    approved: Optional[bool] = True


class JobResponse(BaseModel):
    job_id: str
    company_id: str
    job_type: str
    status: str
    progress: dict
    error: Optional[str] = None


class CompanyDetailResponse(BaseModel):
    id: str
    slug: str
    name: str
    url: str
    status: str
    created_at: str
    profile: Optional[dict] = None
    active_job: Optional[dict] = None


class CompetitorResponse(BaseModel):
    id: str
    company_id: str
    name: str
    domain: Optional[str] = None
    website: Optional[str] = None
    tier: Optional[str] = None
    score: float
    approved: bool
    collected: bool
    created_at: str


class ComparisonDetail(BaseModel):
    id: str
    competitor_id: str
    competitor_name: str
    threat_level: str
    summary: str
    our_advantages: List[str]
    our_disadvantages: List[str]
    their_advantages: List[str]
    their_disadvantages: List[str]
    key_differentiators: List[str]
    is_generic: bool = False
    confidence_score: float = 1.0


class ReportResponse(BaseModel):
    company_id: str
    company_name: str
    url: str
    status: str
    profile: Optional[dict] = None
    competitors: List[CompetitorResponse]
    comparisons: List[ComparisonDetail]
    markdown: str
    generated_at: str

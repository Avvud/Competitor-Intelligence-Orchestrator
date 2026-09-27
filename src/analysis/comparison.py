"""
comparison.py — 1v1 comparison generator (SMART tier).
Compares target company with a specific competitor.
Enforces source verification (unsupported claims moved to data_gaps),
threat presence defaults, and effort/impact validation.
"""
import json
import uuid
from typing import Any
from sqlalchemy.orm import Session

from src.core.db import CompanyProfile as DBCompanyProfile, Competitor, ConnectorRecord, AnalysisResult, Comparison
from src.core.llm_client import LLMClient
from src.core.models import ComparisonOutput, Dimension, Disadvantage, Advantage, ThreatOpportunity

COMPARISON_PROMPT_TEMPLATE = """You are a senior competitive intelligence analyst.
Generate a structured 1v1 competitive comparison report comparing Our Company with Competitor Company.

Our Company Context:
{our_context}

Competitor Company ({competitor_name}, Tier: {competitor_tier}, Score: {threat_score}):
{competitor_context}

Available Verified Source URLs:
{sources_list}

Return a single JSON object matching this exact schema:
{{
  "company_id": "{company_id}",
  "competitor": "{competitor_name}",
  "tier": "{competitor_tier}",
  "threat_score": {threat_score},
  "dimensions": [
    {{
      "name": "Dimension Name (e.g. Pricing, Features, Reach)",
      "us": "Our position",
      "them": "Their position",
      "verdict": "us" or "them" or "tie" or "unknown" or "not_applicable",
      "sources": ["source_url"]
    }}
  ],
  "our_disadvantages": [
    {{
      "point": "Specific point where we lag",
      "evidence": "Supporting detail",
      "sources": ["source_url"]
    }}
  ],
  "their_advantages_to_adopt": [
    {{
      "point": "Advantage we can adopt",
      "effort": "low" or "med" or "high",
      "impact": "low" or "med" or "high",
      "sources": ["source_url"]
    }}
  ],
  "threats": [
    {{
      "point": "Competitive threat description",
      "evidence": "Evidence detail",
      "sources": ["source_url"]
    }}
  ],
  "opportunities": [
    {{
      "point": "Opportunity for us",
      "evidence": "Evidence detail",
      "sources": ["source_url"]
    }}
  ],
  "other_important_points": [],
  "data_gaps": ["Area where data is missing"]
}}

CRITICAL RULES:
1. Only cite source URLs that are in the Provided Available Source URLs list.
2. If no threats are found, return threats with point "None found".
3. Every adoptable advantage MUST include valid effort ("low"|"med"|"high") and impact ("low"|"med"|"high").
"""


def generate_1v1_comparison(
    db: Session,
    llm_client: LLMClient,
    company_id: str,
    competitor_id: str
) -> ComparisonOutput:
    """
    Generate 1v1 comparison between company_id and competitor_id.
    Persists result in DB and returns Pydantic-validated ComparisonOutput.
    """
    # 1. Fetch Our Profile
    our_prof = db.query(DBCompanyProfile).filter(DBCompanyProfile.company_id == company_id).first()
    our_context = f"Name: {our_prof.name if our_prof else company_id}\nIndustry: {our_prof.industry_label if our_prof else 'Unknown'}\nProducts: {our_prof.products if our_prof else '[]'}"

    # 2. Fetch Competitor
    comp = db.query(Competitor).filter(
        Competitor.company_id == company_id,
        Competitor.id == competitor_id
    ).first()
    if not comp:
        raise ValueError(f"Competitor {competitor_id!r} not found for company {company_id!r}")

    comp_tier = (comp.tier or "regional").lower()
    if comp_tier not in ("regional", "state", "national"):
        comp_tier = "regional"
    threat_score = comp.score if comp.score is not None else 50

    # 3. Fetch Connector Records and Analysis Results for stored sources
    records = db.query(ConnectorRecord).filter(
        ConnectorRecord.company_id == company_id,
        ConnectorRecord.competitor_id == competitor_id
    ).all()
    
    stored_sources: set[str] = set()
    comp_data_str = f"Name: {comp.name}\nDomain: {comp.domain}\nWebsite: {comp.website}\n"
    for r in records:
        if r.source_url:
            stored_sources.add(r.source_url)
        if r.raw_data:
            comp_data_str += f"\n--- Connector Source ({r.connector}): {r.source_url} ---\n{r.raw_data[:1000]}\n"

    analysis = db.query(AnalysisResult).filter(
        AnalysisResult.company_id == company_id,
        AnalysisResult.competitor_id == competitor_id
    ).all()
    for a in analysis:
        if a.result_json:
            comp_data_str += f"\n--- Analysis ({a.analysis_type}) ---\n{a.result_json[:1000]}\n"

    sources_list_str = "\n".join(sorted(list(stored_sources))) if stored_sources else "None"

    prompt = COMPARISON_PROMPT_TEMPLATE.format(
        company_id=company_id,
        our_context=our_context,
        competitor_name=comp.name,
        competitor_tier=comp_tier,
        threat_score=threat_score,
        competitor_context=comp_data_str[:4000],
        sources_list=sources_list_str
    )

    llm_res = llm_client.call(
        task_type="write_comparison",
        prompt=prompt,
        company_id=company_id,
        expect_json=True
    )

    try:
        raw_dict = json.loads(llm_res.content)
    except Exception:
        raw_dict = {}

    if not isinstance(raw_dict, dict):
        raw_dict = {}

    # Override/ensure top metadata
    raw_dict["company_id"] = company_id
    raw_dict["competitor"] = comp.name
    raw_dict["tier"] = comp_tier
    raw_dict["threat_score"] = threat_score

    # Source filtering and data gaps migration (T5.2)
    data_gaps: list[str] = raw_dict.get("data_gaps") if isinstance(raw_dict.get("data_gaps"), list) else []

    def filter_sources_and_verify(items: list[dict], item_type: str) -> list[dict]:
        valid_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            claimed = item.get("sources", [])
            if not isinstance(claimed, list):
                claimed = []
            
            # Keep only sources present in stored_sources (if stored_sources is non-empty)
            valid_srcs = [s for s in claimed if s in stored_sources]
            
            # If item claimed sources but NONE were valid stored sources:
            if claimed and stored_sources and not valid_srcs:
                point_text = item.get("point") or item.get("name") or "Unverified claim"
                data_gaps.append(f"Unverified claim in {item_type} (dropped source): {point_text}")
                continue
            
            item["sources"] = valid_srcs
            valid_items.append(item)
        return valid_items

    raw_dict["our_disadvantages"] = filter_sources_and_verify(
        raw_dict.get("our_disadvantages", []), "our_disadvantages"
    )
    raw_dict["their_advantages_to_adopt"] = filter_sources_and_verify(
        raw_dict.get("their_advantages_to_adopt", []), "their_advantages_to_adopt"
    )
    raw_dict["threats"] = filter_sources_and_verify(
        raw_dict.get("threats", []), "threats"
    )
    raw_dict["opportunities"] = filter_sources_and_verify(
        raw_dict.get("opportunities", []), "opportunities"
    )

    # T5.3: Ensure threats section present (default to "None found" if empty)
    if not raw_dict["threats"]:
        raw_dict["threats"] = [{
            "point": "None found",
            "evidence": "Not available",
            "sources": []
        }]

    # T5.4: Ensure effort & impact valid for adoptable advantages
    for adv in raw_dict["their_advantages_to_adopt"]:
        eff = str(adv.get("effort", "med")).lower()
        imp = str(adv.get("impact", "med")).lower()
        adv["effort"] = eff if eff in ("low", "med", "high") else "med"
        adv["impact"] = imp if imp in ("low", "med", "high") else "med"

    raw_dict["data_gaps"] = data_gaps
    raw_dict["lower_quality"] = llm_res.lower_quality

    # Validate with Pydantic
    parsed = ComparisonOutput(**raw_dict)

    # Save to SQLite DB
    existing_comp = db.query(Comparison).filter(
        Comparison.company_id == company_id,
        Comparison.competitor_id == competitor_id
    ).first()

    json_data = parsed.model_dump_json()
    if existing_comp:
        existing_comp.output_json = json_data
        existing_comp.lower_quality = parsed.lower_quality
    else:
        new_rec = Comparison(
            id=str(uuid.uuid4()),
            company_id=company_id,
            competitor_id=competitor_id,
            output_json=json_data,
            lower_quality=parsed.lower_quality
        )
        db.add(new_rec)
    db.commit()

    return parsed

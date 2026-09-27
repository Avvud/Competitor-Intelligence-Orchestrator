"""
rollup.py — Tier roll-up generator for M5.
Consolidates 1v1 comparisons across a tier (regional, state, national)
into a prioritized action list ranked strictly by impact (high > med > low)
and then effort (low > med > high).
"""
import json
import uuid
from typing import Any
from sqlalchemy.orm import Session

from src.core.db import Comparison, Rollup, Competitor
from src.core.llm_client import LLMClient
from src.core.models import RollupOutput, RollupItem

IMPACT_WEIGHT = {"high": 3, "med": 2, "low": 1}
EFFORT_WEIGHT = {"low": 1, "med": 2, "high": 3}


def sort_actions(actions: list[RollupItem]) -> list[RollupItem]:
    """
    Sort action list ranked by impact descending (high > med > low)
    and then effort ascending (low > med > high).
    """
    return sorted(
        actions,
        key=lambda a: (-IMPACT_WEIGHT.get(a.impact, 1), EFFORT_WEIGHT.get(a.effort, 3))
    )


def generate_tier_rollup(
    db: Session,
    llm_client: LLMClient | None,
    company_id: str,
    tier: str
) -> RollupOutput:
    """
    Consolidate 1v1 comparisons for a specific tier and company_id into a RollupOutput.
    Sorts actions by impact and effort.
    """
    tier_norm = tier.lower().strip()
    if tier_norm not in ("regional", "state", "national"):
        tier_norm = "regional"

    # Query competitors in this tier for company_id
    competitors = db.query(Competitor).filter(
        Competitor.company_id == company_id,
        Competitor.tier == tier_norm
    ).all()

    comp_ids = [c.id for c in competitors]

    # Query comparisons for these competitors
    comparisons = db.query(Comparison).filter(
        Comparison.company_id == company_id,
        Comparison.competitor_id.in_(comp_ids)
    ).all() if comp_ids else []

    actions: list[RollupItem] = []

    for comp_rec in comparisons:
        if not comp_rec.output_json:
            continue
        try:
            data = json.loads(comp_rec.output_json)
        except Exception:
            continue

        comp_name = data.get("competitor", "Unknown Competitor")

        # Collect adoptable advantages
        advantages = data.get("their_advantages_to_adopt", [])
        for adv in advantages:
            if not isinstance(adv, dict):
                continue
            pt = adv.get("point")
            if pt:
                eff = str(adv.get("effort", "med")).lower()
                imp = str(adv.get("impact", "med")).lower()
                eff = eff if eff in ("low", "med", "high") else "med"
                imp = imp if imp in ("low", "med", "high") else "med"
                srcs = adv.get("sources", [])
                actions.append(RollupItem(
                    point=pt,
                    competitor=comp_name,
                    effort=eff,
                    impact=imp,
                    sources=srcs if isinstance(srcs, list) else []
                ))

        # Also collect opportunities
        opportunities = data.get("opportunities", [])
        for opp in opportunities:
            if not isinstance(opp, dict):
                continue
            pt = opp.get("point")
            if pt and pt != "None found":
                actions.append(RollupItem(
                    point=pt,
                    competitor=comp_name,
                    effort="med",
                    impact="high",
                    sources=opp.get("sources", []) if isinstance(opp.get("sources"), list) else []
                ))

    # Sort actions by impact desc, effort asc (T5.6)
    sorted_action_list = sort_actions(actions)

    rollup = RollupOutput(
        company_id=company_id,
        tier=tier_norm,
        actions=sorted_action_list
    )

    # Persist to SQLite DB
    existing_rollup = db.query(Rollup).filter(
        Rollup.company_id == company_id,
        Rollup.tier == tier_norm
    ).first()

    json_str = rollup.model_dump_json()
    if existing_rollup:
        existing_rollup.output_json = json_str
    else:
        new_rec = Rollup(
            id=str(uuid.uuid4()),
            company_id=company_id,
            tier=tier_norm,
            output_json=json_str
        )
        db.add(new_rec)
    db.commit()

    return rollup

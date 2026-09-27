"""
pipeline.py — Main analysis pipeline for M4.
Handles pricing extraction, review clustering, feature extraction, chunking,
industry applicability, and hash-based skipping for unchanged inputs.
"""
import hashlib
import json
import uuid
from typing import Any
from sqlalchemy.orm import Session

from src.core.db import AnalysisResult
from src.core.llm_client import LLMClient
from src.analysis.chunker import chunk_text
from src.analysis.pricing import extract_pricing
from src.analysis.reviews import cluster_reviews
from src.analysis.features import extract_feature_matrix


def _hash_input(text: str) -> str:
    """Generate SHA256 hash of input text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_analysis(
    db: Session,
    llm_client: LLMClient,
    company_id: str,
    analysis_type: str,
    text: str,
    competitor_id: str | None = None,
    industry: str = "",
    reviews_list: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """
    Run analysis task (pricing, reviews, or features).
    Checks if input text/content hash is unchanged from previous AnalysisResult.
    If unchanged, returns cached DB result with ZERO LLM calls.
    """
    content_str = text if text else json.dumps(reviews_list or [])
    content_hash = _hash_input(content_str)

    # Check for existing AnalysisResult with matching company_id, competitor_id, analysis_type
    query = db.query(AnalysisResult).filter(
        AnalysisResult.company_id == company_id,
        AnalysisResult.analysis_type == analysis_type
    )
    if competitor_id:
        query = query.filter(AnalysisResult.competitor_id == competitor_id)

    existing = query.first()
    if existing and existing.result_json:
        try:
            stored_data = json.loads(existing.result_json)
            if isinstance(stored_data, dict) and stored_data.get("_content_hash") == content_hash:
                # Content hasn't changed — return stored result without LLM call!
                return stored_data.get("result", stored_data)
        except Exception:
            pass

    # Process based on analysis_type
    if analysis_type == "pricing":
        chunks = chunk_text(text, max_tokens=1500)
        # Process primary chunk (or combine chunk results)
        combined_text = "\n\n".join(chunks)
        res_data = extract_pricing(llm_client, combined_text)

    elif analysis_type == "reviews" or analysis_type == "cluster_reviews":
        revs = reviews_list or []
        res_data = cluster_reviews(llm_client, revs)

    elif analysis_type == "features" or analysis_type == "feature_matrix":
        chunks = chunk_text(text, max_tokens=1500)
        combined_text = "\n\n".join(chunks)
        res_data = extract_feature_matrix(llm_client, combined_text, industry=industry)

    else:
        raise ValueError(f"Unknown analysis_type: {analysis_type!r}")

    # Wrap result with metadata for hash caching
    persisted_payload = {
        "_content_hash": content_hash,
        "result": res_data
    }

    # Save or update AnalysisResult record in DB
    if existing:
        existing.result_json = json.dumps(persisted_payload)
    else:
        rec = AnalysisResult(
            id=str(uuid.uuid4()),
            company_id=company_id,
            competitor_id=competitor_id,
            analysis_type=analysis_type,
            result_json=json.dumps(persisted_payload)
        )
        db.add(rec)
    
    db.commit()
    return res_data

"""
cache.py — caches LLM responses by hash(prompt + model).

Avoids re-calling the LLM for identical inputs. The hash is stored in
the llm_cache table. Works across runs (SQLite is persistent).
"""

import hashlib
import logging
import uuid

from sqlalchemy.orm import Session

from src.core.db import LLMCache

logger = logging.getLogger(__name__)


def _make_hash(prompt: str, model: str) -> str:
    key = f"{model}::{prompt}"
    return hashlib.sha256(key.encode()).hexdigest()


def get_cached(session: Session, prompt: str, model: str) -> str | None:
    """Return cached response string, or None if not cached."""
    h = _make_hash(prompt, model)
    row = session.query(LLMCache).filter_by(prompt_hash=h).first()
    if row:
        logger.debug("Cache HIT for model=%s hash=%s", model, h[:8])
        return row.response
    return None


def save_to_cache(
    session: Session,
    prompt: str,
    model: str,
    response: str,
    company_id: str | None = None,
):
    """Store a response in cache. Silently skip if already cached."""
    h = _make_hash(prompt, model)
    exists = session.query(LLMCache).filter_by(prompt_hash=h).first()
    if exists:
        return
    row = LLMCache(
        id=str(uuid.uuid4()),
        company_id=company_id,
        prompt_hash=h,
        model=model,
        response=response,
    )
    session.add(row)
    session.commit()
    logger.debug("Cache SAVE for model=%s hash=%s", model, h[:8])

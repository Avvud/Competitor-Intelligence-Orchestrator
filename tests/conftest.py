"""
conftest.py — shared pytest fixtures for all tests.

Key design decisions:
- Every test gets a fresh in-memory SQLite DB (no file I/O, no leakage)
- GROQ_API_KEY is set to a fake value so config loading doesn't fail
- Real HTTP is never called — respx mocks all httpx requests
"""

import os

import pytest
from sqlalchemy.orm import Session

from src.core.db import Base, init_db, make_engine, _SessionLocal
import src.core.db as db_module

# ---------------------------------------------------------------------------
# Environment: fake API key so config() doesn't raise
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    """Set all required env vars to fake values for every test."""
    monkeypatch.setenv("GROQ_API_KEY",      "test_FAKE_KEY_abc123")
    monkeypatch.setenv("SMART_MODEL",       "llama-3.3-70b-versatile")
    monkeypatch.setenv("FAST_CLOUD_MODEL",  "llama-3.1-8b-instant")
    monkeypatch.setenv("BULK_MODEL",        "gemma3:4b")
    monkeypatch.setenv("SMART_BASE_URL",    "https://api.groq.com/openai/v1")
    monkeypatch.setenv("BULK_BASE_URL",     "http://localhost:11434/v1")
    monkeypatch.setenv("DB_URL",            "sqlite:///:memory:")


# ---------------------------------------------------------------------------
# In-memory database: fresh for every test
# ---------------------------------------------------------------------------

@pytest.fixture
def db_session(monkeypatch) -> Session:
    """
    Returns a SQLAlchemy session backed by a fresh in-memory SQLite DB.
    Tears down after the test.
    """
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()

    # Patch the module-level globals so any code that calls get_session() gets this
    monkeypatch.setattr(db_module, "_engine", engine)
    monkeypatch.setattr(db_module, "_SessionLocal", SessionLocal)

    yield session

    session.close()
    Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# Budget fixture (uses the db_session above)
# ---------------------------------------------------------------------------

@pytest.fixture
def budget(db_session):
    from src.core.budget import Budget
    return Budget(db_session, daily_requests=10, daily_tokens=50_000)


# ---------------------------------------------------------------------------
# LLM client fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def llm_client(db_session, budget):
    # Reset the cached config so each test starts clean
    from src.core import llm_client as lc_module
    client = lc_module.LLMClient(session=db_session, budget=budget)
    return client


# ---------------------------------------------------------------------------
# Groq mock response helpers
# ---------------------------------------------------------------------------

def groq_ok_response(content: str, model: str = "llama-3.3-70b-versatile") -> dict:
    """Build a minimal OpenAI-compatible success response."""
    return {
        "id": "chatcmpl-test",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "model": model,
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }


def groq_models_response(*model_ids: str) -> dict:
    """Build a /models list response."""
    return {"data": [{"id": mid} for mid in model_ids]}


def ollama_tags_response(*model_names: str) -> dict:
    """Build an Ollama /api/tags response."""
    return {"models": [{"name": n} for n in model_names]}

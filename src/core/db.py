"""
db.py — database setup and all table definitions.

One SQLite file holds data for EVERY company. Every table has a company_id
column so queries never cross company boundaries.
"""

import os
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, String, Text, create_engine
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


# ---------------------------------------------------------------------------
# Engine setup
# ---------------------------------------------------------------------------

def get_db_url() -> str:
    return os.environ.get("DB_URL", "sqlite:///data/competitor_intel.db")


def make_engine(db_url: str | None = None):
    url = db_url or get_db_url()
    # Create parent directory if needed (only for file-based SQLite)
    if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
        path = url.removeprefix("sqlite:///")
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    return create_engine(url, connect_args={"check_same_thread": False})


# Module-level engine & session factory (overridden in tests via init_db)
_engine = None
_SessionLocal = None


def init_db(db_url: str | None = None):
    """Call this once at startup (or in tests with a test URL)."""
    global _engine, _SessionLocal
    _engine = make_engine(db_url)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(_engine)


def get_session() -> Session:
    if _SessionLocal is None:
        init_db()
    return _SessionLocal()


# ---------------------------------------------------------------------------
# ORM base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class Company(Base):
    __tablename__ = "companies"

    id         = Column(String, primary_key=True)
    slug       = Column(String, unique=True, nullable=False)
    name       = Column(String)
    url        = Column(String)
    # pending_profile | confirmed | discovery_done | collection_done | analysis_done | done
    status     = Column(String, default="pending_profile")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CompanyProfile(Base):
    __tablename__ = "company_profiles"

    id               = Column(String, primary_key=True)
    company_id       = Column(String, nullable=False, index=True)  # ★
    name             = Column(String)
    industry_label   = Column(String)
    industry_cat     = Column(String)
    products         = Column(Text)   # JSON string
    target_customers = Column(Text)
    price_band       = Column(String)
    business_model   = Column(String)
    hq_city          = Column(String)
    hq_state         = Column(String)
    hq_country       = Column(String)
    service_areas    = Column(Text)   # JSON string
    social_handles   = Column(Text)   # JSON string
    size_hint        = Column(String)
    founding_year    = Column(Integer)
    search_keywords  = Column(Text)   # JSON string
    confidence       = Column(Text)   # JSON string {field: float}
    source_urls      = Column(Text)   # JSON string {field: url}
    confirmed_at     = Column(DateTime)
    cli_overrides    = Column(Text)   # JSON string
    created_at       = Column(DateTime, default=datetime.utcnow)


class PageSnapshot(Base):
    __tablename__ = "page_snapshots"

    id           = Column(String, primary_key=True)
    company_id   = Column(String, nullable=False, index=True)  # ★
    url          = Column(String)
    content_hash = Column(String)
    raw_html     = Column(Text)
    clean_text   = Column(Text)
    fetched_at   = Column(DateTime, default=datetime.utcnow)
    etag         = Column(String)
    version      = Column(Integer, default=1)


class Competitor(Base):
    __tablename__ = "competitors"

    id         = Column(String, primary_key=True)
    company_id = Column(String, nullable=False, index=True)  # ★
    name       = Column(String)
    domain     = Column(String)
    aliases    = Column(Text)   # JSON string
    tier       = Column(String)  # regional | state | national
    score      = Column(Integer)
    website    = Column(String)
    approved   = Column(Boolean, default=False)
    collected  = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConnectorRecord(Base):
    __tablename__ = "connector_records"

    id            = Column(String, primary_key=True)
    company_id    = Column(String, nullable=False, index=True)  # ★
    competitor_id = Column(String, nullable=False, index=True)
    connector     = Column(String)
    source_url    = Column(String)
    fetched_at    = Column(DateTime, default=datetime.utcnow)
    content_hash  = Column(String)
    raw_data      = Column(Text)   # JSON string
    # ok | empty | unavailable | quota_exhausted | blocked
    status        = Column(String, default="ok")
    version       = Column(Integer, default=1)


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id            = Column(String, primary_key=True)
    company_id    = Column(String, nullable=False, index=True)  # ★
    competitor_id = Column(String)
    analysis_type = Column(String)
    result_json   = Column(Text)   # JSON string
    lower_quality = Column(Boolean, default=False)
    created_at    = Column(DateTime, default=datetime.utcnow)


class Comparison(Base):
    __tablename__ = "comparisons"

    id            = Column(String, primary_key=True)
    company_id    = Column(String, nullable=False, index=True)  # ★
    competitor_id = Column(String)
    output_json   = Column(Text)   # JSON string
    lower_quality = Column(Boolean, default=False)
    created_at    = Column(DateTime, default=datetime.utcnow)


class Rollup(Base):
    __tablename__ = "rollups"

    id          = Column(String, primary_key=True)
    company_id  = Column(String, nullable=False, index=True)  # ★
    tier        = Column(String)
    output_json = Column(Text)
    created_at  = Column(DateTime, default=datetime.utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id          = Column(String, primary_key=True)
    company_id  = Column(String, nullable=False, index=True)  # ★
    job_type    = Column(String)
    # pending | running | queued_budget | done | failed
    status      = Column(String, default="pending")
    progress    = Column(Text)   # JSON string {step, total, message}
    result_json = Column(Text)
    error       = Column(Text)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LLMCache(Base):
    __tablename__ = "llm_cache"

    id          = Column(String, primary_key=True)
    company_id  = Column(String, index=True)   # ★ nullable — shared cache entries
    prompt_hash = Column(String, unique=True, nullable=False)
    model       = Column(String)
    response    = Column(Text)
    created_at  = Column(DateTime, default=datetime.utcnow)


class BudgetLog(Base):
    __tablename__ = "budget_log"

    id          = Column(String, primary_key=True)
    company_id  = Column(String, index=True)   # ★ nullable
    date        = Column(String, nullable=False)  # YYYY-MM-DD
    tier        = Column(String)               # smart | bulk
    requests    = Column(Integer, default=0)
    tokens_in   = Column(Integer, default=0)
    tokens_out  = Column(Integer, default=0)
    model       = Column(String)

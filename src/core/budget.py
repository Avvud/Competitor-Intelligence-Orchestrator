"""
budget.py — tracks daily Groq request and token usage in SQLite.

Simple design: one row per (date, tier, model). We read the row at the
start of each smart-tier call, increment, and check against the daily cap.
"""

import logging
import uuid
from datetime import date

from sqlalchemy.orm import Session

from src.core.db import BudgetLog

logger = logging.getLogger(__name__)

# Defaults — overridden by settings.yaml values loaded in llm_client.py
DEFAULT_DAILY_SMART_REQUESTS = 500
DEFAULT_DAILY_SMART_TOKENS = 100_000


class BudgetExceeded(Exception):
    """Raised when the daily Groq budget is exhausted."""


class Budget:
    def __init__(
        self,
        session: Session,
        daily_requests: int = DEFAULT_DAILY_SMART_REQUESTS,
        daily_tokens: int = DEFAULT_DAILY_SMART_TOKENS,
    ):
        self.session = session
        self.daily_requests = daily_requests
        self.daily_tokens = daily_tokens

    def _today(self) -> str:
        return date.today().isoformat()

    def _get_or_create(self, today: str, tier: str, model: str) -> BudgetLog:
        row = (
            self.session.query(BudgetLog)
            .filter_by(date=today, tier=tier, model=model)
            .first()
        )
        if row is None:
            row = BudgetLog(
                id=str(uuid.uuid4()),
                date=today,
                tier=tier,
                model=model,
                requests=0,
                tokens_in=0,
                tokens_out=0,
            )
            self.session.add(row)
        return row

    def check_smart_budget(self, model: str):
        """Raise BudgetExceeded if today's smart-tier limits are hit."""
        today = self._today()
        row = self._get_or_create(today, "smart", model)
        if row.requests >= self.daily_requests:
            raise BudgetExceeded(
                f"Daily smart-tier request limit reached ({self.daily_requests})"
            )
        if row.tokens_in + row.tokens_out >= self.daily_tokens:
            raise BudgetExceeded(
                f"Daily smart-tier token limit reached ({self.daily_tokens})"
            )

    def record(
        self,
        tier: str,
        model: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        company_id: str | None = None,
    ):
        """Record a completed call. Call this AFTER a successful response."""
        today = self._today()
        row = self._get_or_create(today, tier, model)
        row.company_id = company_id
        row.requests += 1
        row.tokens_in += tokens_in
        row.tokens_out += tokens_out
        self.session.commit()
        logger.debug(
            "Budget: tier=%s model=%s requests=%d tokens_in=%d tokens_out=%d",
            tier, model, row.requests, row.tokens_in, row.tokens_out,
        )

    def get_summary(self, model: str | None = None) -> dict:
        """Return today's usage summary."""
        today = self._today()
        q = self.session.query(BudgetLog).filter_by(date=today)
        if model:
            q = q.filter_by(model=model)
        rows = q.all()
        smart = {"requests": 0, "tokens_in": 0, "tokens_out": 0}
        bulk = {"requests": 0, "tokens_in": 0, "tokens_out": 0}
        for row in rows:
            target = smart if row.tier == "smart" else bulk
            target["requests"] += row.requests
            target["tokens_in"] += row.tokens_in
            target["tokens_out"] += row.tokens_out
        return {
            "date": today,
            "smart": smart,
            "bulk": bulk,
            "daily_request_limit": self.daily_requests,
            "daily_token_limit": self.daily_tokens,
        }

"""
test_m0_foundation.py — M0 foundation tests (T0.1 to T0.12)

All HTTP calls are mocked with respx. No real API keys. No real network.
"""

import json
import logging
import uuid

import httpx
import pytest
import respx

from tests.conftest import groq_ok_response, groq_models_response, ollama_tags_response


# ---------------------------------------------------------------------------
# T0.1 — Missing GROQ_API_KEY: clear error, key not in traceback
# ---------------------------------------------------------------------------

def test_T0_1_missing_api_key(monkeypatch, db_session, budget):
    """Config loading raises a clear RuntimeError when key is absent."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    from src.core import llm_client as lc
    client = lc.LLMClient(session=db_session, budget=budget)
    client._cfg = None  # force re-load

    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        client._config()


def test_T0_1b_key_not_in_exception(monkeypatch, db_session, budget):
    """The actual key value must never appear in the error message."""
    monkeypatch.setenv("GROQ_API_KEY", "sk-real-secret-key")

    from src.core import llm_client as lc
    client = lc.LLMClient(session=db_session, budget=budget)
    client._cfg = None

    # Config loads fine; now trigger an HTTP error that might echo the key
    with respx.mock:
        respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(401, json={"error": "invalid key sk-real-secret-key"})
        )
        try:
            client._call_smart("hello")
        except Exception as exc:
            assert "sk-real-secret-key" not in str(exc), "Key must not appear in exception"


# ---------------------------------------------------------------------------
# T0.2 — Router: extract_profile → bulk, write_comparison → smart
# ---------------------------------------------------------------------------

def test_T0_2_router_extract_profile():
    from src.core.router import route
    assert route("extract_profile") == "bulk"


def test_T0_2_router_write_comparison():
    from src.core.router import route
    assert route("write_comparison") == "smart"


def test_T0_2_router_unknown_task():
    from src.core.router import route
    with pytest.raises(ValueError, match="Unknown task type"):
        route("nonexistent_task")


# ---------------------------------------------------------------------------
# T0.3 — 429 with Retry-After 2: waits ≥ 2 s (fake clock), retries, succeeds
# ---------------------------------------------------------------------------

def test_T0_3_retry_after(db_session, budget, monkeypatch):
    """On 429, the limiter waits the Retry-After duration, then retries."""
    slept = []

    def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("time.sleep", fake_sleep)
    # Also patch monotonic to avoid real timing
    mono_values = iter([0.0, 0.0, 3.0, 3.0, 3.0])
    monkeypatch.setattr("time.monotonic", lambda: next(mono_values, 3.0))

    from src.core import llm_client as lc
    client = lc.LLMClient(session=db_session, budget=budget)

    with respx.mock:
        route = respx.post("https://api.groq.com/openai/v1/chat/completions")
        route.side_effect = [
            httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate_limit"}),
            httpx.Response(200, json=groq_ok_response("done")),
        ]
        result = client._call_smart("hello")

    assert result.content == "done"
    # At least one sleep of ≥ 2 seconds should have occurred
    assert any(s >= 2.0 for s in slept), f"Expected sleep ≥ 2s, got: {slept}"


# ---------------------------------------------------------------------------
# T0.4 — Groq budget exhausted: smart task → BudgetExceeded, bulk unaffected
# ---------------------------------------------------------------------------

def test_T0_4_budget_exhausted(db_session, monkeypatch):
    """When smart budget is 0 requests, smart calls fail; bulk continues."""
    from src.core.budget import Budget, BudgetExceeded
    from src.core import llm_client as lc

    # Budget with 0 allowed requests
    tight_budget = Budget(db_session, daily_requests=0, daily_tokens=0)
    client = lc.LLMClient(session=db_session, budget=tight_budget)

    # Bulk should still work (Ollama doesn't check budget)
    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
    mono = iter([0.0] * 50)
    monkeypatch.setattr("time.monotonic", lambda: next(mono, 0.0))

    with respx.mock:
        respx.post("http://localhost:11434/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=groq_ok_response("bulk ok", model="gemma3:4b"))
        )
        result = client._call_bulk("hello")
    assert result.content == "bulk ok"

    # Smart should raise (or fall back to bulk if Ollama is up)
    # If Ollama is mocked, it falls back; if not — BudgetExceeded
    # The job status must not crash — just raise cleanly
    with respx.mock:
        respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=groq_ok_response("smart ok"))
        )
        respx.post("http://localhost:11434/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=groq_ok_response("fallback ok", model="gemma3:4b"))
        )
        # Budget is 0 — smart is blocked; it falls back to bulk (Ollama)
        result = client._call_smart("needs smart")
    # Result comes from fallback — lower quality flagged
    assert result.lower_quality is True


# ---------------------------------------------------------------------------
# T0.5 — Invalid JSON twice then valid: repaired within 2 retries
# ---------------------------------------------------------------------------

def test_T0_5_invalid_json_repaired(db_session, budget, monkeypatch):
    """BULK tier: bad JSON on attempts 0 and 1, valid on attempt 2."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    from src.core import llm_client as lc
    client = lc.LLMClient(session=db_session, budget=budget)

    bad  = httpx.Response(200, json=groq_ok_response("{broken", model="gemma3:4b"))
    good = httpx.Response(200, json=groq_ok_response('{"key": "value"}', model="gemma3:4b"))

    with respx.mock:
        respx.post("http://localhost:11434/v1/chat/completions").mock(
            side_effect=[bad, bad, good]
        )
        result = client._call_bulk("extract something", expect_json=True)

    assert json.loads(result.content) == {"key": "value"}


# ---------------------------------------------------------------------------
# T0.6 — Invalid JSON three times: fails with reason, no infinite loop
# ---------------------------------------------------------------------------

def test_T0_6_invalid_json_always_fails(db_session, budget, monkeypatch):
    """BULK tier: three bad JSON responses → RuntimeError, stops."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    from src.core import llm_client as lc
    client = lc.LLMClient(session=db_session, budget=budget)

    bad = httpx.Response(200, json=groq_ok_response("not json at all", model="gemma3:4b"))

    with respx.mock:
        respx.post("http://localhost:11434/v1/chat/completions").mock(
            side_effect=[bad, bad, bad]
        )
        with pytest.raises(RuntimeError, match="invalid JSON"):
            client._call_bulk("extract something", expect_json=True)


# ---------------------------------------------------------------------------
# T0.7 — Ollama connection refused: falls back to fast cloud model, flagged
# ---------------------------------------------------------------------------

def test_T0_7_ollama_down_fallback(db_session, budget, monkeypatch):
    """When Ollama is unreachable, bulk calls use Groq fast model + lower_quality."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    from src.core import llm_client as lc
    client = lc.LLMClient(session=db_session, budget=budget)

    with respx.mock:
        respx.post("http://localhost:11434/v1/chat/completions").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=groq_ok_response("cloud fallback"))
        )
        result = client._call_bulk("summarise something")

    assert result.lower_quality is True
    assert "cloud fallback" in result.content


# ---------------------------------------------------------------------------
# T0.8 — Same prompt+model twice: second call from cache, zero HTTP calls
# ---------------------------------------------------------------------------

def test_T0_8_cache_hit(db_session, budget, monkeypatch):
    """Second identical call must be served from cache with no HTTP request."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    from src.core import llm_client as lc
    client = lc.LLMClient(session=db_session, budget=budget)

    with respx.mock:
        mock_route = respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=groq_ok_response("cached response"))
        )
        r1 = client._call_smart("same prompt")
        r2 = client._call_smart("same prompt")

    assert r1.content == r2.content == "cached response"
    assert r2.from_cache is True
    assert mock_route.call_count == 1   # only one real HTTP call


# ---------------------------------------------------------------------------
# T0.9 — Logs during error: API key never appears
# ---------------------------------------------------------------------------

def test_T0_9_key_not_in_logs(db_session, budget, monkeypatch, caplog):
    """The API key must never appear in any log output."""
    fake_key = "sk-super-secret-key-abc123"
    monkeypatch.setenv("GROQ_API_KEY", fake_key)

    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    from src.core import llm_client as lc
    client = lc.LLMClient(session=db_session, budget=budget)
    client._cfg = None   # force re-load with new key

    with caplog.at_level(logging.WARNING):
        with respx.mock:
            respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
                return_value=httpx.Response(
                    500,
                    json={"error": f"internal error, key={fake_key}"}
                )
            )
            try:
                client._call_smart("hello")
            except Exception:
                pass   # we only care about logs

    for record in caplog.records:
        assert fake_key not in record.getMessage(), \
            f"API key leaked in log: {record.getMessage()}"


# ---------------------------------------------------------------------------
# T0.10 — Two companies: queries by company_id return no cross-company rows
# ---------------------------------------------------------------------------

def test_T0_10_company_isolation(db_session):
    """Competitors inserted for company A must not appear in company B queries."""
    from src.core.db import Competitor

    cid_a = str(uuid.uuid4())
    cid_b = str(uuid.uuid4())

    db_session.add(Competitor(
        id=str(uuid.uuid4()), company_id=cid_a,
        name="Rival A1", domain="rival-a1.com", tier="regional", score=80,
    ))
    db_session.add(Competitor(
        id=str(uuid.uuid4()), company_id=cid_b,
        name="Rival B1", domain="rival-b1.com", tier="national", score=60,
    ))
    db_session.commit()

    a_rows = db_session.query(Competitor).filter_by(company_id=cid_a).all()
    b_rows = db_session.query(Competitor).filter_by(company_id=cid_b).all()

    assert len(a_rows) == 1 and a_rows[0].name == "Rival A1"
    assert len(b_rows) == 1 and b_rows[0].name == "Rival B1"

    # Cross-check: B's competitor not in A's results
    a_names = {r.name for r in a_rows}
    b_names = {r.name for r in b_rows}
    assert a_names.isdisjoint(b_names)


# ---------------------------------------------------------------------------
# T0.11 — --check with mocks: prints status of both tiers and model presence
# ---------------------------------------------------------------------------

def test_T0_11_check_command(monkeypatch, capsys):
    """check_tiers() should report both tiers and whether models are found."""
    monkeypatch.setattr("time.sleep", lambda s: None)

    with respx.mock:
        # Groq /models
        respx.get("https://api.groq.com/openai/v1/models").mock(
            return_value=httpx.Response(
                200, json=groq_models_response("llama-3.3-70b-versatile", "llama-3.1-8b-instant")
            )
        )
        # Ollama /api/tags
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=httpx.Response(200, json=ollama_tags_response("gemma3:4b"))
        )

        from src.core.llm_client import check_tiers
        results = check_tiers(verbose=True)

    out = capsys.readouterr().out
    assert "SMART" in out
    assert "BULK"  in out
    assert results["smart"]["ok"] is True
    assert results["bulk"]["ok"]  is True


# ---------------------------------------------------------------------------
# T0.12 — Configured model missing from /models: error lists alternatives
# ---------------------------------------------------------------------------

def test_T0_12_model_not_found(monkeypatch):
    """If the configured model isn't in the /models list, error shows alternatives."""
    monkeypatch.setenv("SMART_MODEL", "nonexistent-model-xyz")

    with respx.mock:
        respx.get("https://api.groq.com/openai/v1/models").mock(
            return_value=httpx.Response(
                200, json=groq_models_response("llama-3.3-70b-versatile", "llama-3.1-8b-instant")
            )
        )
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=httpx.Response(200, json=ollama_tags_response("gemma3:4b"))
        )

        from src.core.llm_client import check_tiers
        results = check_tiers(verbose=False)

    assert results["smart"]["ok"] is False
    assert "nonexistent-model-xyz" in results["smart"]["error"]
    # Must list at least one alternative
    assert len(results["smart"]["available_models"]) > 0

"""
llm_client.py — the single place that talks to LLMs.

Two tiers:
  SMART — Groq cloud (llama-3.3-70b-versatile by default)
  BULK  — local Ollama / Gemma (no tool calls, plain JSON output)

Handles:
  - Missing API key → clear error, key never logged
  - 429 with Retry-After → wait and retry
  - Exponential backoff for other errors
  - Daily budget check (SMART only)
  - Response cache (both tiers)
  - JSON repair loop (up to 2 retries, BULK tier)
  - Fallback: Ollama down → Groq fast model + lower_quality flag
  - Startup check: GET /models and /api/tags; verify configured models exist
  - Secret redaction from all log/error output

Usage:
  python -m src.core.llm_client --check
"""

import json
import logging
import os
import re
import sys
from typing import Any

import httpx

from src.core.budget import Budget, BudgetExceeded
from src.core.cache import get_cached, save_to_cache
from src.core.models import LLMResult
from src.core.rate_limiter import RateLimiter, backoff_sleep
from src.core.router import route

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

class _RedactingFormatter(logging.Formatter):
    """Log formatter that removes API key values from output."""

    def __init__(self, *args, secrets: list[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._secrets = secrets or []

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            if secret:
                text = text.replace(secret, "***REDACTED***")
        return text


def install_redacting_formatter(keys: list[str]):
    """
    Wrap all existing handlers with a redacting formatter.
    Call once at startup with the list of secrets to hide.
    """
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    for handler in logging.root.handlers:
        handler.setFormatter(_RedactingFormatter(fmt, secrets=keys))
    # Also apply to any future handlers added to the root logger
    logging.root._redact_secrets = keys  # type: ignore[attr-defined]


def _redact(text: str) -> str:
    """Remove API key-looking strings from a message before logging."""
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]{10,}", r"\1***REDACTED***", text)
    text = re.sub(r"(api.?key[=:\s]+)[A-Za-z0-9_\-\.]{10,}", r"\1***REDACTED***", text, flags=re.I)
    for k in ("GROQ_API_KEY", "GEMINI_API_KEY", "FALLBACK_API_KEY"):
        val = os.environ.get(k, "")
        if val:
            text = text.replace(val, "***REDACTED***")
    return text


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _get_config() -> dict[str, str]:
    """Load env vars. Supports GROQ_API_KEY and GEMINI_API_KEY."""
    from dotenv import load_dotenv
    load_dotenv()
    groq_key = os.environ.get("GROQ_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not groq_key and not gemini_key:
        raise RuntimeError(
            "Neither GROQ_API_KEY nor GEMINI_API_KEY is set. "
            "Add GROQ_API_KEY or GEMINI_API_KEY to your .env file."
        )
    return {
        "groq_api_key":     groq_key,
        "gemini_api_key":   gemini_key,
        "smart_base_url":   os.environ.get("SMART_BASE_URL", "https://api.groq.com/openai/v1"),
        "smart_model":      os.environ.get("SMART_MODEL", "llama-3.3-70b-versatile"),
        "fast_cloud_model": os.environ.get("FAST_CLOUD_MODEL", "llama-3.1-8b-instant"),
        "bulk_base_url":    os.environ.get("BULK_BASE_URL", "http://localhost:11434/v1"),
        "bulk_model":       os.environ.get("BULK_MODEL", "gemma3:4b"),
        "fallback_base_url":os.environ.get("FALLBACK_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"),
        "fallback_api_key": os.environ.get("FALLBACK_API_KEY", gemini_key),
        "fallback_model":   os.environ.get("FALLBACK_MODEL", "gemini-2.5-flash"),
    }


# ---------------------------------------------------------------------------
# Low-level HTTP call
# ---------------------------------------------------------------------------

def _chat_completion(
    base_url: str,
    model: str,
    prompt: str,
    api_key: str,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """
    POST /chat/completions and return the parsed JSON response dict.
    Raises httpx.HTTPStatusError for non-2xx responses.
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# JSON repair helper
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> str:
    """
    Try to pull a JSON object or array from a string.
    Returns the cleaned JSON string, or raises ValueError.
    """
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\n?", "", text.strip(), flags=re.M)
    text = re.sub(r"\n?```$", "", text.strip(), flags=re.M)
    text = text.strip()
    # Try direct parse
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    # Try to find first {...} or [...]
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end > start:
            candidate = text[start:end + 1]
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass
    raise ValueError("Could not extract valid JSON from LLM response")


# ---------------------------------------------------------------------------
# Main LLM client
# ---------------------------------------------------------------------------

class LLMClient:
    """
    Simple LLM client. Create one instance per pipeline run, passing in
    a SQLAlchemy session for cache and budget tracking.
    """

    def __init__(self, session, budget: Budget | None = None):
        self.session = session
        self.budget = budget
        self._limiter = RateLimiter(
            requests_per_minute=int(os.environ.get("GROQ_RPM", "25"))
        )
        self._cfg: dict[str, str] | None = None   # loaded lazily

    def _config(self) -> dict[str, str]:
        if self._cfg is None:
            self._cfg = _get_config()
            # Install redacting formatter now that we know the keys
            install_redacting_formatter([
                self._cfg["groq_api_key"],
                self._cfg["fallback_api_key"],
            ])
        return self._cfg

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def call(
        self,
        task_type: str,
        prompt: str,
        company_id: str | None = None,
        expect_json: bool = False,
    ) -> LLMResult:
        """
        Make an LLM call for the given task type.
        Handles routing, caching, budget, rate-limiting, and fallbacks.

        If expect_json=True and the tier is BULK, attempts up to 2 JSON
        repair retries before raising.
        """
        tier = route(task_type)

        if tier == "smart":
            return self._call_smart(prompt, company_id=company_id, expect_json=expect_json)
        else:
            return self._call_bulk(prompt, company_id=company_id, expect_json=expect_json)

    # ------------------------------------------------------------------
    # Smart tier (Groq)
    # ------------------------------------------------------------------

    def _call_smart(
        self,
        prompt: str,
        company_id: str | None = None,
        expect_json: bool = False
    ) -> LLMResult:
        cfg = self._config()
        model = cfg["smart_model"]

        # 1. Cache check
        cached = get_cached(self.session, prompt, model)
        if cached:
            return LLMResult(content=cached, model=model, tier="smart", from_cache=True)

        # 2. Budget check
        if self.budget:
            try:
                self.budget.check_smart_budget(model)
            except BudgetExceeded as exc:
                logger.warning("Smart budget exceeded: %s", exc)
                # Try falling back to local bulk with lower_quality flag
                try:
                    result = self._call_bulk(prompt, company_id=company_id, expect_json=expect_json)
                    result.lower_quality = True
                    return result
                except Exception:
                    raise exc  # re-raise original budget error

        # 3. Call with retries
        max_retries = 5
        last_exception = None
        for attempt in range(max_retries):
            try:
                self._limiter.wait_if_needed()
                raw = _chat_completion(
                    cfg["smart_base_url"], model, prompt, cfg["groq_api_key"]
                )
                content = raw["choices"][0]["message"]["content"]
                tokens_in  = raw.get("usage", {}).get("prompt_tokens", 0)
                tokens_out = raw.get("usage", {}).get("completion_tokens", 0)

                if self.budget:
                    self.budget.record("smart", model, tokens_in, tokens_out, company_id)

                save_to_cache(self.session, prompt, model, content, company_id)
                return LLMResult(
                    content=content, model=model, tier="smart",
                    tokens_in=tokens_in, tokens_out=tokens_out,
                )

            except httpx.HTTPStatusError as exc:
                last_exception = exc
                if exc.response.status_code == 429:
                    retry_after = float(
                        exc.response.headers.get("Retry-After", 2)
                    )
                    self._limiter.handle_retry_after(retry_after)
                    continue
                logger.error("Groq HTTP error: %s", _redact(str(exc)))
                if attempt < max_retries - 1:
                    backoff_sleep(attempt)
                else:
                    break

            except Exception as exc:
                last_exception = exc
                logger.error("Groq error (attempt %d): %s", attempt, _redact(str(exc)))
                if attempt < max_retries - 1:
                    backoff_sleep(attempt)
                else:
                    break

        # If Groq call failed after all retries, try Gemini API if available, else local bulk tier
        if cfg.get("gemini_api_key"):
            try:
                logger.warning("Groq failed after retries. Falling back to Gemini API...")
                gemini_url = cfg["fallback_base_url"]
                gemini_key = cfg["gemini_api_key"]
                gemini_model = cfg["fallback_model"]
                raw = _chat_completion(gemini_url, gemini_model, prompt, api_key=gemini_key)
                content = raw["choices"][0]["message"]["content"]
                tokens_in  = raw.get("usage", {}).get("prompt_tokens", 0)
                tokens_out = raw.get("usage", {}).get("completion_tokens", 0)
                save_to_cache(self.session, prompt, gemini_model, content, company_id)
                return LLMResult(
                    content=content, model=gemini_model, tier="smart",
                    tokens_in=tokens_in, tokens_out=tokens_out
                )
            except Exception as g_exc:
                logger.warning("Gemini API fallback failed: %s", _redact(str(g_exc)))

        try:
            logger.warning("Falling back to local bulk tier.")
            result = self._call_bulk(prompt, company_id=company_id, expect_json=expect_json)
            result.lower_quality = True
            return result
        except Exception:
            if last_exception:
                raise last_exception
            raise RuntimeError("Smart tier: all retries exhausted")

    # ------------------------------------------------------------------
    # Bulk tier (local Ollama)
    # ------------------------------------------------------------------

    def _call_bulk(
        self,
        prompt: str,
        company_id: str | None = None,
        expect_json: bool = False,
    ) -> LLMResult:
        cfg = self._config()
        model = cfg["bulk_model"]
        base_url = cfg["bulk_base_url"]

        # Cache check
        cached = get_cached(self.session, prompt, model)
        if cached:
            return LLMResult(content=cached, model=model, tier="bulk", from_cache=True)

        # Try Ollama, fall back to fast cloud model if unreachable
        try:
            content = self._ollama_call(base_url, model, prompt)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            logger.warning(
                "Ollama unreachable (%s). Trying fast cloud model.", _redact(str(exc))
            )
            return self._bulk_fallback_to_cloud(prompt, company_id, expect_json)

        # JSON repair loop (BULK tier only)
        if expect_json:
            content = self._repair_json(content, prompt, model, base_url, cfg)

        save_to_cache(self.session, prompt, model, content, company_id)
        if self.budget:
            self.budget.record("bulk", model, company_id=company_id)

        return LLMResult(content=content, model=model, tier="bulk")

    def _ollama_call(self, base_url: str, model: str, prompt: str) -> str:
        """Single Ollama or bulk cloud call. Raises on connection error or timeout."""
        cfg = self._config()
        api_key = cfg["groq_api_key"] if ("groq" in base_url.lower() or "openai" in base_url.lower()) else "ollama"
        raw = _chat_completion(base_url, model, prompt, api_key=api_key, timeout=300.0)
        return raw["choices"][0]["message"]["content"]

    def _bulk_fallback_to_cloud(
        self, prompt: str, company_id: str | None, expect_json: bool
    ) -> LLMResult:
        """Fall back to Groq fast model when Ollama is down."""
        cfg = self._config()
        fast_model = cfg["fast_cloud_model"]

        if self.budget:
            self.budget.check_smart_budget(fast_model)

        self._limiter.wait_if_needed()
        raw = _chat_completion(
            cfg["smart_base_url"], fast_model, prompt, cfg["groq_api_key"]
        )
        content = raw["choices"][0]["message"]["content"]
        tokens_in  = raw.get("usage", {}).get("prompt_tokens", 0)
        tokens_out = raw.get("usage", {}).get("completion_tokens", 0)

        if self.budget:
            self.budget.record("smart", fast_model, tokens_in, tokens_out, company_id)

        save_to_cache(self.session, prompt, fast_model, content, company_id)
        return LLMResult(
            content=content, model=fast_model, tier="bulk",
            lower_quality=True, tokens_in=tokens_in, tokens_out=tokens_out,
        )

    def _repair_json(
        self, content: str, prompt: str, model: str, base_url: str, cfg: dict
    ) -> str:
        """
        Try to get valid JSON from the model output.
        On failure, ask the model to fix it (up to 2 repair attempts total).
        """
        for attempt in range(3):   # attempt 0 = first response, 1+2 = repairs
            try:
                return _extract_json(content)
            except ValueError:
                if attempt >= 2:
                    raise RuntimeError(
                        f"LLM returned invalid JSON after {attempt} repair attempts. "
                        f"Last output: {content[:200]!r}"
                    )
                repair_prompt = (
                    f"Your previous response was not valid JSON. "
                    f"Return ONLY valid JSON, no other text.\n\n"
                    f"Previous response:\n{content}\n\nOriginal task:\n{prompt}"
                )
                logger.warning("JSON repair attempt %d for model %s", attempt + 1, model)
                content = self._ollama_call(base_url, model, repair_prompt)
        # unreachable
        raise RuntimeError("JSON repair loop exited unexpectedly")


# ---------------------------------------------------------------------------
# Startup check
# ---------------------------------------------------------------------------

def check_tiers(verbose: bool = True) -> dict[str, Any]:
    """
    Check that both tiers are reachable and configured models exist.
    Returns a status dict. Prints results if verbose=True.
    """
    results: dict[str, Any] = {}

    # We need the config even for --check
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        results["smart"] = {"ok": False, "error": "GROQ_API_KEY not set"}
    else:
        smart_url  = os.environ.get("SMART_BASE_URL", "https://api.groq.com/openai/v1")
        smart_model = os.environ.get("SMART_MODEL", "llama-3.3-70b-versatile")
        results["smart"] = _check_groq(smart_url, key, smart_model)

    bulk_url  = os.environ.get("BULK_BASE_URL", "http://localhost:11434/v1")
    bulk_model = os.environ.get("BULK_MODEL", "gemma3:4b")
    if "groq" in bulk_url.lower() or "openai" in bulk_url.lower():
        results["bulk"] = _check_groq(bulk_url, key, bulk_model)
    else:
        results["bulk"] = _check_ollama(bulk_url, bulk_model)

    if verbose:
        _print_check_results(results)

    return results


def _check_groq(base_url: str, api_key: str, model: str) -> dict:
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            available = [m["id"] for m in resp.json().get("data", [])]
            model_ok = model in available or any(model in m for m in available)
            return {
                "ok": model_ok,
                "model": model,
                "model_found": model_ok,
                "available_models": available[:10],  # first 10 to keep output short
                "error": None if model_ok else f"Model '{model}' not found. Available: {available[:5]}",
            }
    except Exception as exc:
        return {"ok": False, "model": model, "error": _redact(str(exc))}


def _check_ollama(base_url: str, model: str) -> dict:
    # Ollama uses /api/tags, not /v1/models
    tags_url = base_url.replace("/v1", "").rstrip("/") + "/api/tags"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(tags_url)
            resp.raise_for_status()
            available = [m["name"] for m in resp.json().get("models", [])]
            # Ollama model names can include tag e.g. "gemma3:4b"
            model_ok = any(model in name for name in available)
            return {
                "ok": model_ok,
                "model": model,
                "model_found": model_ok,
                "available_models": available,
                "error": None if model_ok else f"Model '{model}' not found. Available: {available}",
            }
    except Exception as exc:
        return {"ok": False, "model": model, "error": str(exc)}


def _print_check_results(results: dict):
    print("\n=== LLM Tier Check ===")
    for tier, info in results.items():
        status = "✓ OK" if info.get("ok") else "✗ FAIL"
        model = info.get("model", "?")
        print(f"  [{tier.upper()}] {status}  model={model}")
        if not info.get("ok"):
            print(f"         Error: {info.get('error')}")
    print()


# ---------------------------------------------------------------------------
# CLI entry point: python -m src.core.llm_client --check
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--check" in sys.argv:
        from dotenv import load_dotenv
        load_dotenv()
        results = check_tiers(verbose=True)
        all_ok = all(v.get("ok") for v in results.values())
        sys.exit(0 if all_ok else 1)
    else:
        print("Usage: python -m src.core.llm_client --check")
        sys.exit(1)

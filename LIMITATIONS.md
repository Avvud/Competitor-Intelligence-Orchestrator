# LIMITATIONS.md — Operational Boundaries & Rate Limits

This document outlines known operational boundaries, rate limit handling, and API fallback policies.

---

## 1. LLM Budget & Rate Limits

- **Daily Budget Limits**:
  - Request limit: 500 requests / day (default).
  - Token limit: 500,000 tokens / day (default).
- **Rate Limit (429) Handling**:
  - Exponential backoff with `Retry-After` header parsing.
  - Automatic fallback between Groq models and local/ollama models when remote quotas are hit.

---

## 2. Dynamic Rendering & Anti-Bot Protections

- **Playwright Fallback**: Static HTTP requests (`httpx`) are tried first. If a page requires JavaScript rendering or returns anti-bot challenges (e.g. Cloudflare JS challenge), the browser connector attempts Playwright rendering.
- **Blocked Platforms**: Sites requiring authentication (e.g. LinkedIn private profiles, walled Instagram accounts) are gracefully recorded with `status: blocked` and reported under data gaps without crashing execution.

---

## 3. Provider & Model Fallback Guide

If Groq API limits change or model names update:
1. Update `GROQ_PRIMARY_MODEL` or `GROQ_SMART_MODEL` in `.env`.
2. The model router will automatically fall back to Ollama or secondary providers if Groq returns 429/404 errors.

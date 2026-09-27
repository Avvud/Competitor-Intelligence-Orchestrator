# LEGAL_NOTES.md — Scraping & Intelligence Gathering Policy

This document details the legal, ethical, and compliance guidelines governing the **Competitor Intelligence Orchestrator**.

---

## 1. Terms of Service & Scraping Compliance

- **Robots.txt Adherence**: The system checks `robots.txt` before fetching public Web pages. Paths marked as `Disallow` are respected and skipped.
- **Rate Limiting & Politeness**: Requests to target domains are rate-limited with mandatory minimum inter-request delays to prevent server overload or Denial of Service (DoS).
- **Public Data Only**: Data collection is strictly limited to publicly accessible Web pages, public APIs, RSS feeds, and official social media posts. The orchestrator never bypasses authentication, paywalls, or login screens.

---

## 2. IP & Copyright Guidelines

- **Fair Use**: Collected text and metrics are used solely for analytical summarization, tier classification, and competitive benchmarking.
- **Attribution**: All stored claims, pricing matrices, and insights maintain direct linkage to the source URL and page snapshot version (`PageSnapshot.id`).

---

## 3. Privacy & Personal Data (GDPR / CCPA)

- **No PII Collection**: The system excludes personal identifiable information (PII) such as personal emails, phone numbers, or private employee identities.
- **Corporate Entity Scope**: Intelligence collection focuses exclusively on corporate entities, pricing tiers, product feature matrices, and public press releases.

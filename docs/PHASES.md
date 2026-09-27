# PHASES.md — Competitor Intelligence Orchestrator

> Last updated: P0 complete — awaiting user approval before M0 begins.

| Phase | Name | Status | Recommended Model | Backup | Test IDs | Gate | Depends On |
|---|---|---|---|---|---|---|---|
| P0 | Plan | ✅✅ Approved | Claude Sonnet 4.6 (Thinking) | — | TP0.1–TP0.4 (review, no code) | User approves plan | — |
| M0 | Foundation | ✅✅ Done — 15/15 passed | Sonnet 4.6 (Thinking) | GPT-OSS 120B (Medium) | T0.1–T0.12 | ✅ All pass | P0 |
| M1 | URL → Profile | ✅✅ Done — 10/10 passed | Gemini 3.6 Flash (High) | Claude Sonnet 4.6 (Thinking) | T1.1–T1.9 | ✅ All pass | M0 |
| M2 | Discovery & Scoring | ✅✅ Done — 10/10 passed | Claude Sonnet 4.6 (Thinking) | GPT-OSS 120B (Medium) | T2.1–T2.9 | ✅ All pass | M1 |
| M3 | Connectors | ✅✅ Done — 13/13 passed | Gemini 3.6 Flash (High) | GPT-OSS 120B (Medium) | T3.1–T3.10 | ✅ All pass | M2 |
| M4 | Analysis (bulk) | ✅✅ Done — 7/7 passed | Gemini 3.6 Flash (High) | Gemini 3.1 Pro (Low) | T4.1–T4.7 | ✅ All pass | M3 |
| M5 | 1v1 Comparison & Roll-up | ✅✅ Done — 8/8 passed | Claude Sonnet 4.6 (Thinking) | Claude Opus 4.6 (Thinking) | T5.1–T5.8 | ✅ All pass | M4 |
| M6 | MCP Server & Hermes | ✅✅ Done — 6/6 passed | Gemini 3.1 Pro (High) | Claude Opus 4.6 (Thinking) | T6.1–T6.6 (pytest); T6.7 (manual) | ✅ All pytest pass | M5 |
| M7 | Dashboard, Reports & Alerts | ✅✅ Done — 8/8 passed | Gemini 3.6 Flash (High) | — | T7.1–T7.8 | ✅ All pass | M5, M6 |
| M8 | Hardening & Acceptance | ✅✅ Done — 4/4 passed | Gemini 3.1 Pro (High) | Claude Sonnet 4.6 (Thinking) | T8.1–T8.4 | ✅ All pass | M7 |

## Dependency Graph

```
P0 → M0 → M1 → M2 → M3 → M4 → M5 ─┬─ M6 ─┐
                                      └─ M7 ─┴─ M8
```

## Status Legend
- 🔲 Not started
- 🔄 In progress
- ✅ Done — awaiting approval
- ✅✅ Approved
- ❌ Blocked

## Phase End-Report Template (< 15 lines)
```
Phase: <id> | Model used: <name>
Tests: <N> passed, <N> failed
Files changed: <list>
Limitations: <1-3 bullet points>
Next phase needs: <1-3 items>
```

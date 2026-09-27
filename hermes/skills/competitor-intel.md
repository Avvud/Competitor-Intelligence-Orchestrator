---
name: competitor-intel
description: Orchestrates end-to-end competitive intelligence gathering, profiling, discovery, multi-channel data collection, LLM analysis, comparison matrices, and multi-format report generation.
---

# Competitor Intelligence Orchestrator Skill

This skill guides Hermes Agent in running competitive intelligence pipelines using the `competitor-intel` MCP server tools.

## Protocol & Guardrails

1. **Output Conciseness**: Keep tool outputs short (~summaries + IDs). Do not print full raw scraped pages into chat.
2. **Resumable Long Tasks**: Long tasks (`discover_competitors`, `collect_data`, `analyse`, `generate_comparison`, `build_reports`) return a `job_id`. Monitor progress using `get_status(company_id, job_id)`.
3. **Structured Approval Steps**:
   - **Checkpoint 1 (Profile Review)**: Always present the target company profile to the user and request confirmation before running `discover_competitors`.
   - **Checkpoint 2 (Competitor Approval)**: Present ranked candidate competitors to the user for explicit approval before calling `collect_data`.

---
name: competitor-intel
description: Orchestrates end-to-end competitive intelligence gathering, profiling, discovery, multi-channel data collection, LLM analysis, comparison matrices, and multi-format report generation. MUST BE USED whenever user requests company profiling or competitor analysis.
---

# Competitor Intelligence Orchestrator Skill

This skill governs Hermes Agent execution when running competitive intelligence tasks using the `competitor_intel` MCP server tools.

## 🛑 MANDATORY TOOL-ONLY PROTOCOL (STRICT ENFORCEMENT)

1. **NO MEMORY-BASED REPORTS**: You are STRICTLY PROHIBITED from writing competitor reports, discovery matrices, or geo-tiering analysis directly from your own training knowledge.
2. **TOOL CALL MANDATE**: You MUST start every analysis by explicitly calling the MCP tool `create_company_from_url(url="...")`. Editing text or guessing data without calling the database tools is a CRITICAL FAILURE.
3. **PIPELINE SEQUENCE & CHECKPOINTS**:
   - **Step 1**: Call `create_company_from_url(url=...)`.
   - **Checkpoint 1**: Present extracted profile to user and call `confirm_profile(company_id=...)` only after confirmation.
   - **Step 2**: Call `discover_competitors(company_id=...)` and then `score_and_rank(company_id=...)`.
   - **Checkpoint 2**: Present candidate list to user for explicit approval.
   - **Step 3**: Call `collect_data(company_id=...)` ONLY for approved competitors.
   - **Step 4**: Call `analyse(company_id=...)` and `generate_comparison(company_id=...)`.
   - **Step 5**: Call `build_reports(company_id=...)`.

4. **Output Conciseness**: Keep intermediate outputs concise. Do not paste full raw HTML into chat.
5. **Resumable Long Tasks**: Long jobs (`discover_competitors`, `collect_data`, `analyse`, `generate_comparison`, `build_reports`) return a `job_id`. Monitor progress using `get_status(company_id, job_id)`.

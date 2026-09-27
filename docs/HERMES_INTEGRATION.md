# Competitor Intelligence Orchestrator — Architecture & Hermes WSL Integration Guide

This guide provides a comprehensive overview of the **Competitor Intelligence Orchestrator** codebase, an explanation of all 12 MCP tools, and step-by-step instructions to connect this system with **Hermes Agent in WSL (Windows Subsystem for Linux)** using **Groq API** or a **Local LLM (Ollama)**.

---

## 1. Codebase Architecture & File Overview

The system is designed around a modular Python backend using SQLAlchemy, Pydantic, and an MCP (Model Context Protocol) stdio interface.

```
Competitor Intelligence Orchestrator/
├── config/                  # Configuration files & API schemas
├── data/                    # SQLite database (competitor_intel.db)
├── docs/                    # Architectural specs, phase descriptions & integration docs
├── hermes/
│   └── skills/
│       └── competitor-intel.md  # Hermes Agent skill definition
├── pyproject.toml           # Project dependencies & build metadata
└── src/
    ├── mcp/
    │   └── server.py        # Stdio MCP Server defining 12 tools for Hermes
    ├── core/                # Core infrastructure components
    │   ├── db.py            # SQLite ORM models (Company, Competitor, Job, BudgetLog)
    │   ├── llm_client.py    # LLM Router (Smart Groq vs Bulk Ollama/Groq tier calls)
    │   ├── budget.py        # Token & request budget tracker & enforcer
    │   ├── models.py        # Pydantic data models & job schemas
    │   ├── cache.py         # Disk cache for HTTP requests & page scraping
    │   └── rate_limiter.py  # Domain & host rate limiting
    ├── profile/             # Target Company Profiling Engine
    │   ├── url_ingest.py    # Web scraper extracting HTML & clean text
    │   ├── infer_profile.py # JSON-LD, OpenGraph & LLM profile generator
    │   ├── geo.py           # Geo-location inference (phone, TLD, currency)
    │   └── industry.py      # Industry classification & normalisation
    ├── discovery/           # Competitor Candidate Discovery & Scoring
    │   ├── pipeline.py      # Multi-source discovery orchestrator
    │   ├── sources.py       # Web search & directory query adapters
    │   ├── query_gen.py     # LLM keyword & search query generator
    │   ├── dedup.py         # Canonical URL deduplication & grouping
    │   └── scorer.py        # Strategic relevance scoring & tiering
    ├── connectors/          # Data Collection Connectors
    │   ├── runner.py        # Parallel connector execution manager
    │   ├── base.py          # Abstract connector base class
    │   ├── website.py       # Website scraper & product page parser
    │   ├── youtube.py       # YouTube transcript & video metadata scraper
    │   ├── instagram_bd.py   # Social media profile scraper
    │   ├── news_rss.py      # News RSS feed collector
    │   └── manual_entry.py  # Manual data entry fallback handler
    ├── analysis/            # Strategic Intelligence & LLM Analysis
    │   ├── pipeline.py      # Analysis job runner
    │   ├── pricing.py       # Tier, model & currency pricing parser
    │   ├── features.py      # Feature matrix extractor
    │   ├── reviews.py       # Customer sentiment & review extractor
    │   ├── comparison.py    # 1v1 matrix generator
    │   ├── rollup.py        # Tier-level aggregation
    │   └── chunker.py       # Large document text chunker
    ├── reports/             # Multi-format Report Builder
    │   ├── builder.py       # Multi-format report builder (PDF, PPTX, Markdown)
    │   └── markdown.py      # Executive Markdown report renderer
    └── alerts/              # Delta Tracking & Alerts
        └── detector.py      # Competitor strategy & pricing change detector
```

---

## 2. Explanation of the 12 MCP Functions & Workflow

The stdio server (`src/mcp/server.py`) exposes **12 Model Context Protocol tools** that Hermes invokes during execution.

| Tool Name | Key Arguments | Purpose & Internal Functionality |
| :--- | :--- | :--- |
| `create_company_from_url` | `url`, `name`, `industry`, `hq_*` | Initializes target company in DB and queues background profile extraction. Returns `company_id`. |
| `confirm_profile` | `company_id`, `overrides` | **Human Checkpoint 1**: Confirms inferred company profile. Accepts optional JSON overrides. |
| `discover_competitors` | `company_id` | Enforces profile confirmation, then launches multi-source candidate discovery. Returns `job_id`. |
| `score_and_rank` | `company_id` | Ranks candidates using strategic relevance scoring (Tiers 1–3). |
| `collect_data` | `company_id` | Runs scrapers **only on approved competitors**. |
| `analyse` | `company_id` | Runs bulk LLM extraction for pricing models, feature matrices, and customer reviews. |
| `generate_comparison` | `company_id` | Generates 1v1 head-to-head comparison matrices and tier rollups. |
| `build_reports` | `company_id`, `formats` | Compiles reports into **Markdown, PDF, and PPTX**. |
| `detect_changes` | `company_id` | Scans historical data vs current snapshots for pricing/positioning updates. |
| `get_status` | `company_id`, `job_id` | Returns progress and status of background jobs. |
| `get_budget` | `company_id` | Returns daily token usage, total requests made, and remaining quotas. |
| `list_companies` | None | Lists all target companies currently stored in the database. |

---

## 3. Step-by-Step Guide: Easy WSL Integration (Steps 3 to 7)

Since you have already completed **Step 1** (navigating to `/mnt/d/projects/AI/Competitor Intelligence Orchestrator`) and **Step 2** (`uv sync`), proceed with the remaining steps below:

### Step 3: Configure `.env` with Groq API Key / Local LLM

This codebase is natively configured to use **Groq** for high-speed reasoning (Smart tier) and fast cloud fallback (Bulk tier).

#### Option A: You have a Groq API Key (Recommended & Fastest)
Run this command in WSL terminal (replace `gsk_your_groq_key_here` with your actual Groq key):

```bash
cat << 'EOF' > "/mnt/d/projects/AI/Competitor Intelligence Orchestrator/.env"
# === LLM Provider (Groq) ===
GROQ_API_KEY="gsk_your_groq_key_here"

# Smart tier (Groq Cloud)
SMART_BASE_URL="https://api.groq.com/openai/v1"
SMART_MODEL="llama-3.3-70b-versatile"
FAST_CLOUD_MODEL="llama-3.1-8b-instant"

# Bulk tier (Local Ollama or Groq fallback)
# If local Ollama is down, system automatically uses FAST_CLOUD_MODEL (Groq llama-3.1-8b-instant)
BULK_BASE_URL="http://localhost:11434/v1"
BULK_MODEL="gemma3:4b"

# === Database & Budget Limits ===
DB_URL="sqlite:////mnt/d/projects/AI/Competitor Intelligence Orchestrator/data/competitor_intel.db"
DAILY_REQUEST_LIMIT=500
DAILY_TOKEN_LIMIT=500000
EOF
```

#### Option B: You want 100% Local LLM (Ollama)
If you have Ollama running locally in WSL (`ollama serve` and `ollama run llama3.2` or `ollama run gemma3:4b`):

```bash
cat << 'EOF' > "/mnt/d/projects/AI/Competitor Intelligence Orchestrator/.env"
# Groq key dummy check pass
GROQ_API_KEY="ollama"

# Point Smart tier to local Ollama endpoint
SMART_BASE_URL="http://localhost:11434/v1"
SMART_MODEL="llama3.2"
FAST_CLOUD_MODEL="llama3.2"

# Bulk tier
BULK_BASE_URL="http://localhost:11434/v1"
BULK_MODEL="llama3.2"

# Database
DB_URL="sqlite:////mnt/d/projects/AI/Competitor Intelligence Orchestrator/data/competitor_intel.db"
DAILY_REQUEST_LIMIT=5000
DAILY_TOKEN_LIMIT=5000000
EOF
```

---

### Step 3.5: Verify LLM Configuration

Run the built-in diagnostic tool to test your Groq / Ollama setup:

```bash
uv run python -m src.core.llm_client --check
```

*Expected output:*
```
=== LLM Tier Check ===
  [SMART] ✓ OK  model=llama-3.3-70b-versatile
  [BULK]  ✓ OK  model=gemma3:4b (or falling back to fast cloud model)
```

---

### Step 4: Register MCP Server in Hermes Config (`~/.hermes/config.toml`)

Make sure `data` directory exists for the SQLite database:
```bash
mkdir -p "/mnt/d/projects/AI/Competitor Intelligence Orchestrator/data"
```

Now append the MCP server block to your Hermes configuration file in WSL (`~/.hermes/config.toml`):

```bash
# Register the MCP server using Hermes CLI
hermes mcp add competitor_intel \
  --command ~/.local/bin/uv \
  --args "run" "python" "-m" "src.mcp.server" \
  --cwd "/mnt/d/projects/AI/Competitor Intelligence Orchestrator"
```

---

### Step 5: Install the Hermes Skill File

Copy the skill definition file into your Hermes skills directory in WSL:

```bash
mkdir -p ~/.hermes/skills
cp "/mnt/d/projects/AI/Competitor Intelligence Orchestrator/hermes/skills/competitor-intel.md" ~/.hermes/skills/
```

---

### Step 6: Disable Conflicting Native Hermes Tools

To prevent Hermes from confusing native web search with domain-specific competitor intelligence tools:

```bash
hermes tools disable search
hermes tools disable browser
```

---

### Step 7: Launch Hermes & Run Your First Analysis

Set your Groq API key for Hermes Agent itself (if using Groq for Hermes), then run:

```bash
export GROQ_API_KEY="gsk_your_groq_key_here"
hermes -z "Analyse https://stripe.com and prepare the competitor report"
```

#### What Hermes will do:
1. Calls `create_company_from_url` and extracts the profile for Stripe.
2. **Pauses at Checkpoint 1**: Displays profile summary and asks for your confirmation.
3. You type *"Confirmed"* or call `confirm_profile`.
4. Hermes discovers competitors (`discover_competitors` & `score_and_rank`).
5. **Pauses at Checkpoint 2**: Displays candidate list (e.g. Adyen, PayPal).
6. You approve the candidate list, and Hermes completes `collect_data`, `analyse`, `generate_comparison`, and `build_reports`.

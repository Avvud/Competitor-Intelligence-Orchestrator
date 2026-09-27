# Competitor Intelligence Orchestrator ⚡

Autonomous Multi-Agent Competitive Intelligence System built for multi-channel discovery, deep LLM analysis, comparison matrices, multi-format report generation, and Hermes Agent integration.

---

## 🚀 Quickstart & Fresh-Clone Setup

### Prerequisites

- **Python**: 3.11+
- **uv**: Package installer (`pip install uv` or via script)
- **Ollama** (optional local LLM): `ollama serve`
- **Hermes Agent** (optional MCP host)

### 1. Environment Setup

```bash
# Clone the repository
git clone https://github.com/your-org/competitor-intel-orchestrator.git
cd competitor-intel-orchestrator

# Create virtual environment & install dependencies
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install core and dev dependencies
uv pip install -e .
uv pip install -e .[dev,dashboard]
```

### 2. Configure Environment Variables

Create `.env` in the root directory:

```env
GROQ_API_KEY=your_groq_api_key_here
SERPER_API_KEY=your_serper_api_key_here
DAILY_REQUEST_LIMIT=500
DAILY_TOKEN_LIMIT=500000
DB_URL=sqlite:///data/competitor_intel.db
```

---

## 🏃 Running the Application

### 1. Streamlit Dashboard

```bash
uv run streamlit run src/ui/app.py
```

### 2. MCP Server (for Hermes integration)

```bash
uv run python -m src.mcp.server
```

### 3. Running Unit Tests

```bash
uv run pytest -v
```

---

## 📚 Documentation

- [HERMES_INTEGRATION.md](docs/HERMES_INTEGRATION.md) — Hermes Agent MCP setup & manual workflow guide.
- [LEGAL_NOTES.md](LEGAL_NOTES.md) — Scraping policy, robots.txt adherence, & GDPR compliance.
- [LIMITATIONS.md](LIMITATIONS.md) — LLM budget limits, rate limiting (429), & model fallback policies.

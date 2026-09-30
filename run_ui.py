"""
run_ui.py — Single-command entry point to launch the Competitor Intelligence Web UI.
Run with: python run_ui.py
"""

import sys
import os
import logging
import uvicorn
from dotenv import load_dotenv

# Load environment variables FIRST, before anything else
load_dotenv()

# Add repository root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configure logging so background thread errors are visible in the terminal
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
# Reduce noise from third-party libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

from src.core.db import init_db

def _check_env():
    """Print diagnostic summary of environment variables."""
    keys_to_check = {
        "GROQ_API_KEY":   os.environ.get("GROQ_API_KEY", ""),
        "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY", ""),
        "SERPER_API_KEY": os.environ.get("SERPER_API_KEY", ""),
        "DEMO_MODE":      os.environ.get("DEMO_MODE", "false"),
        "SMART_BASE_URL": os.environ.get("SMART_BASE_URL", "(default)"),
        "SMART_MODEL":    os.environ.get("SMART_MODEL", "(default)"),
        "BULK_BASE_URL":  os.environ.get("BULK_BASE_URL", "(default)"),
        "BULK_MODEL":     os.environ.get("BULK_MODEL", "(default)"),
        "DB_URL":         os.environ.get("DB_URL", "(default)"),
    }
    print("\n🔑 Environment Check:")
    all_ok = True
    for key, val in keys_to_check.items():
        if "API_KEY" in key:
            if val:
                display = f"{val[:8]}...{val[-4:]}" if len(val) > 12 else "***set***"
                status = "✅"
            else:
                display = "NOT SET"
                status = "❌"
                all_ok = False
        else:
            display = val or "(empty)"
            status = "ℹ️ "
        print(f"  {status} {key}: {display}")

    if not all_ok:
        print("\n⚠️  WARNING: Some API keys are missing. Check your .env file.")
    else:
        print("\n✅ All API keys configured.")
    print()


if __name__ == "__main__":
    _check_env()
    init_db()
    print("=" * 70)
    print("⚡ Competitor Intelligence Orchestrator Web UI Starting...")
    print("🌐 Open URL in your browser: http://localhost:8000")
    print("=" * 70)
    uvicorn.run("src.web.app:app", host="127.0.0.1", port=8000, reload=False)

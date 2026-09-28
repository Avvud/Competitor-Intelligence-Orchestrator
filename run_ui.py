"""
run_ui.py — Single-command entry point to launch the Competitor Intelligence Web UI.
Run with: python run_ui.py
"""

import sys
import os
import uvicorn
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add repository root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.db import init_db

if __name__ == "__main__":
    init_db()
    print("=" * 70)
    print("⚡ Competitor Intelligence Orchestrator Web UI Starting...")
    print("🌐 Open URL in your browser: http://localhost:8000")
    print("=" * 70)
    uvicorn.run("src.web.app:app", host="127.0.0.1", port=8000, reload=False)

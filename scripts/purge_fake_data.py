"""
purge_fake_data.py — Purge mock/fake pipeline data from SQLite database.

Usage:
  python scripts/purge_fake_data.py --yes
"""

import sys
import os
import shutil
import argparse
from datetime import datetime

# Add repository root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.db import (
    get_session, get_db_url, Company, CompanyProfile, Competitor,
    ConnectorRecord, AnalysisResult, Comparison, Rollup, Job
)


def main():
    parser = argparse.ArgumentParser(description="Purge fake/mock data from SQLite database.")
    parser.add_argument("--yes", action="store_true", help="Confirm deletion of fake data rows.")
    args = parser.parse_args()

    if not args.yes:
        print("❌ Error: You must specify --yes to confirm purging fake data.")
        sys.exit(1)

    db_url = get_db_url()
    print(f"📦 Database URL: {db_url}")

    if db_url.startswith("sqlite:///"):
        db_path = db_url.removeprefix("sqlite:///")
        if os.path.exists(db_path):
            backup_path = f"{db_path}.bak_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(db_path, backup_path)
            print(f"💾 Created database backup at: {backup_path}")

    session = get_session()
    try:
        deleted_comps = session.query(Competitor).delete()
        deleted_conns = session.query(ConnectorRecord).delete()
        deleted_analyses = session.query(AnalysisResult).delete()
        deleted_comparisons = session.query(Comparison).delete()
        deleted_rollups = session.query(Rollup).delete()
        deleted_jobs = session.query(Job).delete()

        # Reset company statuses to pending_profile if profile not confirmed
        companies = session.query(Company).all()
        for c in companies:
            prof = session.query(CompanyProfile).filter(CompanyProfile.company_id == c.id).first()
            if not prof or not prof.confirmed_at:
                c.status = "pending_profile"
            else:
                c.status = "confirmed"

        session.commit()
        print("✅ Purge completed successfully:")
        print(f"   - Deleted {deleted_comps} competitors")
        print(f"   - Deleted {deleted_conns} connector records")
        print(f"   - Deleted {deleted_analyses} analysis results")
        print(f"   - Deleted {deleted_comparisons} comparisons")
        print(f"   - Deleted {deleted_rollups} rollups")
        print(f"   - Deleted {deleted_jobs} jobs")
        print(f"   - Reset {len(companies)} company statuses to valid checkpoint states")
    except Exception as exc:
        session.rollback()
        print(f"❌ Error during purge: {exc}")
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()

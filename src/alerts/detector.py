"""
detector.py — Weekly change detection and alert engine.
Detects price changes, new posts, ads, news across page snapshots and sends digest emails.
"""

import os
import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from typing import Any, Dict, List

from src.core.db import get_session, Company, PageSnapshot, Competitor


def detect_snapshot_changes(company_id: str) -> List[Dict[str, Any]]:
    """Compare page snapshots for a company to detect price or content changes."""
    db = get_session()
    try:
        snapshots = db.query(PageSnapshot).filter(
            PageSnapshot.company_id == company_id
        ).order_by(PageSnapshot.fetched_at.desc()).all()

        changes = []
        # Group snapshots by URL
        by_url: Dict[str, List[PageSnapshot]] = {}
        for s in snapshots:
            by_url.setdefault(s.url, []).append(s)

        for url, sn_list in by_url.items():
            if len(sn_list) >= 2:
                latest = sn_list[0]
                prev = sn_list[1]
                if latest.content_hash != prev.content_hash:
                    # Check if clean text mentions pricing changes
                    l_text = (latest.clean_text or "").lower()
                    p_text = (prev.clean_text or "").lower()

                    change_type = "content_updated"
                    if "$" in l_text or "price" in l_text or "plan" in l_text:
                        if l_text != p_text:
                            change_type = "price_changed"

                    changes.append({
                        "url": url,
                        "change_type": change_type,
                        "previous_hash": prev.content_hash,
                        "latest_hash": latest.content_hash,
                        "detected_at": latest.fetched_at.isoformat() if latest.fetched_at else datetime.utcnow().isoformat(),
                        "summary": f"Change detected on {url} ({change_type})"
                    })

        return changes
    finally:
        db.close()


def send_digest_email(
    to_email: str,
    subject: str,
    body: str,
    smtp_config: Dict[str, Any] | None = None
) -> bool:
    """Send alert digest email via SMTP (supports SMTP mock / custom server)."""
    cfg = smtp_config or {}
    host = cfg.get("host", os.environ.get("SMTP_HOST", "localhost"))
    port = int(cfg.get("port", os.environ.get("SMTP_PORT", "1025")))
    sender = cfg.get("sender", os.environ.get("SMTP_SENDER", "alerts@competitor-intel.ai"))

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email

    # Check for custom mock sender hook
    mock_sender = cfg.get("mock_send_fn")
    if mock_sender:
        mock_sender(sender, [to_email], msg.as_string())
        return True

    try:
        with smtplib.SMTP(host, port) as server:
            server.sendmail(sender, [to_email], msg.as_string())
        return True
    except Exception:
        # Gracefully handle connection error in environments without active SMTP
        return False

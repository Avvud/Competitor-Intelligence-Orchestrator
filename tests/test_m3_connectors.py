"""
test_m3_connectors.py — M3 tests T3.1 to T3.10

No real HTTP calls — all mocked with respx or monkeypatch.
"""

import logging
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from src.connectors.base import (
    ConnectorRecord, BaseConnector, PolicyError, check_policy, BLOCKED_DOMAINS
)
from src.connectors.website import WebsiteConnector
from src.connectors.news_rss import NewsRSSConnector
from src.connectors.youtube import YouTubeConnector
from src.connectors.instagram_bd import InstagramBDConnector
from src.connectors.manual_entry import ManualEntryConnector
from src.connectors.host_limiter import HostRateLimiter

FIXTURES = Path(__file__).parent / "fixtures"
CID  = str(uuid.uuid4())
COMP = str(uuid.uuid4())

VALID_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>Rival Corp launches new feature</title><link>https://rival.com/news/1</link><pubDate>Thu, 25 Sep 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""


# ---------------------------------------------------------------------------
# T3.1 — Every connector returns ConnectorRecord (contract test)
# ---------------------------------------------------------------------------

@respx.mock
def test_T3_1_all_connectors_return_record(monkeypatch):
    """Every connector's fetch() must return a ConnectorRecord."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("time.monotonic", lambda: 100.0)

    # Website
    respx.mock.get("https://rival.com/").mock(
        return_value=httpx.Response(200, text="<html><body>Hello</body></html>",
                                    headers={"content-type": "text/html"})
    )
    r = WebsiteConnector(delay_s=0).fetch(CID, COMP, "https://rival.com/")
    assert isinstance(r, ConnectorRecord), "WebsiteConnector must return ConnectorRecord"
    assert r.status in ("ok", "empty", "unavailable", "quota_exhausted", "blocked")

    # News RSS
    respx.mock.get("https://rival.com/rss").mock(
        return_value=httpx.Response(200, text=VALID_RSS)
    )
    r = NewsRSSConnector(delay_s=0).fetch(CID, COMP, "https://rival.com/rss")
    assert isinstance(r, ConnectorRecord)

    # YouTube (no API key — returns unavailable)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    r = YouTubeConnector(delay_s=0).fetch(CID, COMP, "https://youtube.com/channel/UC123")
    assert isinstance(r, ConnectorRecord)
    assert r.status == "unavailable"

    # Instagram BD (no token — returns unavailable)
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    r = InstagramBDConnector(delay_s=0).fetch(CID, COMP, "@rivalcorp")
    assert isinstance(r, ConnectorRecord)
    assert r.status == "unavailable"

    # Manual entry
    r = ManualEntryConnector().fetch(CID, COMP, "manual:test", data={"name": "Rival"})
    assert isinstance(r, ConnectorRecord)
    assert r.status == "ok"


# ---------------------------------------------------------------------------
# T3.2 — Two requests to same host: gap ≥ configured delay
# ---------------------------------------------------------------------------

def test_T3_2_host_rate_limit():
    """Per-host limiter must enforce minimum gap between requests."""
    times_slept = []
    real_monotonic = time.monotonic

    limiter = HostRateLimiter()
    slept = []

    with patch("src.connectors.host_limiter.time.sleep", side_effect=lambda s: slept.append(s)), \
         patch("src.connectors.host_limiter.time.monotonic", side_effect=[
             0.0,    # now (before first request)
             0.0,    # record timestamp
             0.5,    # now (before second request) — only 0.5s has passed
             2.0,    # record timestamp after sleep
         ]):
        limiter.wait_for_host("example.com", delay_s=2.0)
        limiter.wait_for_host("example.com", delay_s=2.0)

    # A sleep of at least 1.5s must have occurred on the second call
    assert len(slept) >= 1, "Must have slept between requests"
    assert slept[0] >= 1.4, f"Expected sleep ≥ 1.5s, got {slept[0]}"


# ---------------------------------------------------------------------------
# T3.3 — Unchanged page: no re-parse, returns status="empty"
# ---------------------------------------------------------------------------

@respx.mock
def test_T3_3_unchanged_content_no_reparse(monkeypatch):
    """Same content hash → status='empty', no new processing."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    html = "<html><body>Same content</body></html>"
    from src.connectors.base import content_hash as chash
    h = chash(html)

    respx.mock.get("https://rival.com/").mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )
    r = WebsiteConnector(delay_s=0).fetch(
        CID, COMP, "https://rival.com/", prev_hash=h
    )
    assert r.status == "empty", "Unchanged content must return status='empty'"
    assert r.content_hash == h


# ---------------------------------------------------------------------------
# T3.4 — YouTube quota error: status="quota_exhausted", pipeline continues
# ---------------------------------------------------------------------------

@respx.mock
def test_T3_4_youtube_quota_exhausted(monkeypatch):
    """YouTube 403 quota error → status='quota_exhausted', does not raise."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake_yt_key")
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    respx.mock.get("https://www.googleapis.com/youtube/v3/channels").mock(
        return_value=httpx.Response(403, json={
            "error": {
                "errors": [{"reason": "quotaExceeded", "domain": "youtube.quota"}],
                "message": "The caller does not have permission"
            }
        })
    )
    r = YouTubeConnector(delay_s=0).fetch(
        CID, COMP, "https://youtube.com/channel/UC1234567890123456789012"
    )
    assert r.status == "quota_exhausted", f"Expected quota_exhausted, got {r.status}"
    assert r.error_message is not None


# ---------------------------------------------------------------------------
# T3.5 — Instagram BD without permission: status="unavailable", suggests manual
# ---------------------------------------------------------------------------

def test_T3_5_instagram_bd_no_token(monkeypatch):
    """Instagram BD without token returns unavailable and suggests manual_entry."""
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    r = InstagramBDConnector(delay_s=0).fetch(CID, COMP, "@rivalcorp")
    assert r.status == "unavailable"
    assert "manual_entry" in (r.error_message or "").lower(), \
        "Must suggest manual_entry connector"


@respx.mock
def test_T3_5_instagram_bd_permission_denied(monkeypatch):
    """Instagram BD 403 → unavailable + manual_entry suggestion."""
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "fake_token")
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    respx.mock.get("https://graph.facebook.com/v19.0/me").mock(
        return_value=httpx.Response(403, json={"error": {"message": "Permission denied"}})
    )
    r = InstagramBDConnector(delay_s=0).fetch(CID, COMP, "@rivalcorp")
    assert r.status == "unavailable"
    assert "manual_entry" in (r.error_message or "").lower()


# ---------------------------------------------------------------------------
# T3.6 — Registering a LinkedIn or Instagram scraper: raises PolicyError
# ---------------------------------------------------------------------------

def test_T3_6_linkedin_blocked():
    """Fetching linkedin.com must raise PolicyError or return status='blocked'."""
    r = WebsiteConnector(delay_s=0).fetch(CID, COMP, "https://linkedin.com/company/rival")
    assert r.status == "blocked", f"Expected blocked, got {r.status}"


def test_T3_6_instagram_direct_blocked():
    """Fetching instagram.com directly must return status='blocked'."""
    r = WebsiteConnector(delay_s=0).fetch(CID, COMP, "https://instagram.com/rivalcorp")
    assert r.status == "blocked"


def test_T3_6_check_policy_raises():
    """check_policy() must raise PolicyError for blocked domains."""
    with pytest.raises(PolicyError):
        check_policy("https://www.linkedin.com/in/someone")
    with pytest.raises(PolicyError):
        check_policy("https://x.com/rivalcorp")
    with pytest.raises(PolicyError):
        check_policy("https://facebook.com/rivalpage")


# ---------------------------------------------------------------------------
# T3.7 — Manual CSV with 2 bad rows: bad rows rejected, good rows imported
# ---------------------------------------------------------------------------

def test_T3_7_manual_csv_bad_rows(caplog):
    """CSV with 2 bad rows: those rows logged + rejected, good rows imported."""
    csv_path = FIXTURES / "manual_entry_bad_rows.csv"

    with caplog.at_level(logging.WARNING, logger="src.connectors.manual_entry"):
        r = ManualEntryConnector().fetch(
            CID, COMP, str(csv_path), csv_path=str(csv_path)
        )

    assert r.status == "ok", f"Expected ok, got {r.status}"
    # Good rows must be in raw_data
    assert r.raw_data.get("name") == "Rival Corp"
    assert r.raw_data.get("website") == "https://rivalcorp.com"
    # Bad rows must be logged
    assert any("bad rows" in rec.getMessage().lower() or "skipped" in rec.getMessage().lower()
               for rec in caplog.records), "Bad rows must be logged"
    # Bad rows must NOT appear in the data
    assert "" not in r.raw_data
    assert None not in r.raw_data


# ---------------------------------------------------------------------------
# T3.8 — Page content changes: new snapshot version stored, old version kept
# ---------------------------------------------------------------------------

@respx.mock
def test_T3_8_content_change_version_bumped(monkeypatch):
    """When content changes, version must be incremented."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    old_html = "<html><body>Old content</body></html>"
    new_html = "<html><body>New content — prices updated</body></html>"
    old_hash = WebsiteConnector(delay_s=0)  # just for reference

    from src.connectors.base import content_hash as chash
    old_h = chash(old_html)

    respx.mock.get("https://rival.com/").mock(
        return_value=httpx.Response(200, text=new_html, headers={"content-type": "text/html"})
    )
    r = WebsiteConnector(delay_s=0).fetch(
        CID, COMP, "https://rival.com/", prev_hash=old_h, prev_version=1
    )
    assert r.status == "ok", "Changed content must return status='ok'"
    assert r.version == 2, f"Version must be bumped to 2, got {r.version}"
    assert r.content_hash != old_h


# ---------------------------------------------------------------------------
# T3.9 — Connector disabled in settings: never called
# ---------------------------------------------------------------------------

def test_T3_9_disabled_connector_not_called(tmp_path, monkeypatch):
    """A disabled connector must never be instantiated or called."""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(
        "connectors:\n"
        "  website:  { enabled: false, delay_s: 1.0 }\n"
        "  news_rss: { enabled: true,  delay_s: 1.0 }\n"
    )

    called = []

    # Patch WebsiteConnector to record if called
    original_fetch = WebsiteConnector.fetch
    def spy_fetch(self, *args, **kwargs):
        called.append("website")
        return original_fetch(self, *args, **kwargs)

    monkeypatch.setattr(WebsiteConnector, "fetch", spy_fetch)

    from src.connectors.runner import run_connectors
    results = run_connectors(
        company_id=CID,
        competitor_id=COMP,
        urls={"website": "https://rival.com/", "news_rss": ""},
        settings_path=settings_file,
    )

    assert "website" not in called, "Disabled connector must never be called"
    # No website result in output
    assert not any(r.connector == "website" for r in results)


# ---------------------------------------------------------------------------
# T3.10 — Network failure in one connector: others complete, failure logged
# ---------------------------------------------------------------------------

def test_T3_10_network_failure_others_continue(tmp_path, monkeypatch, caplog):
    """One connector raising a network error must not stop others from running."""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(
        "connectors:\n"
        "  website:      { enabled: true, delay_s: 0 }\n"
        "  manual_entry: { enabled: true }\n"
    )

    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    # Website raises a network error
    def bad_fetch(self, *args, **kwargs):
        raise ConnectionError("Network unreachable")
    monkeypatch.setattr(WebsiteConnector, "_fetch", bad_fetch)

    from src.connectors.runner import run_connectors
    with caplog.at_level(logging.WARNING, logger="src.connectors.runner"):
        results = run_connectors(
            company_id=CID,
            competitor_id=COMP,
            urls={
                "website":      "https://rival.com/",
                "manual_entry": "manual:test",
            },
            settings_path=settings_file,
            extra_kwargs={"manual_entry": {"data": {"name": "Rival"}}},
        )

    # Website must appear as unavailable
    web_results = [r for r in results if r.connector == "website"]
    assert web_results, "Website connector result must be in output"
    assert web_results[0].status == "unavailable"

    # manual_entry must have completed successfully
    manual_results = [r for r in results if r.connector == "manual_entry"]
    assert manual_results, "manual_entry must still run"
    assert manual_results[0].status == "ok"

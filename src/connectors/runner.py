"""
runner.py — runs all enabled connectors for a competitor.

Reads enabled flags from settings.yaml. Disabled connectors are never
called — not even instantiated. Each connector failure is caught and
logged; others continue to completion.

Returns list of ConnectorRecord — one per connector attempted.
"""

import logging
from pathlib import Path

import yaml

from src.connectors.base import ConnectorRecord
from src.connectors.website import WebsiteConnector
from src.connectors.news_rss import NewsRSSConnector
from src.connectors.youtube import YouTubeConnector
from src.connectors.instagram_bd import InstagramBDConnector
from src.connectors.manual_entry import ManualEntryConnector

logger = logging.getLogger(__name__)

_DEFAULT_SETTINGS_PATH = Path(__file__).parent.parent.parent / "config" / "settings.yaml"


def load_connector_settings(settings_path: str | Path | None = None) -> dict:
    """Load connector toggles from settings.yaml. Returns empty dict on failure."""
    path = Path(settings_path or _DEFAULT_SETTINGS_PATH)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return raw.get("connectors", {})
    except Exception as exc:
        logger.warning("Could not load settings from %s: %s", path, exc)
        return {}


def run_connectors(
    company_id: str,
    competitor_id: str,
    urls: dict[str, str],          # connector_name → url
    settings_path: str | Path | None = None,
    extra_kwargs: dict | None = None,
) -> list[ConnectorRecord]:
    """
    Run all enabled connectors for one competitor.

    urls: maps connector name to the URL/handle for that connector.
          e.g. {"website": "https://rival.com", "youtube": "UCxxx..."}
    extra_kwargs: optional per-connector kwargs (e.g. prev_hash, csv_path).

    Returns one ConnectorRecord per connector that was enabled AND had a URL.
    """
    settings = load_connector_settings(settings_path)
    extra = extra_kwargs or {}
    results: list[ConnectorRecord] = []

    # Connector registry
    connector_classes = {
        "website":      WebsiteConnector,
        "news_rss":     NewsRSSConnector,
        "youtube":      YouTubeConnector,
        "instagram_bd": InstagramBDConnector,
        "manual_entry": ManualEntryConnector,
    }

    for name, cls in connector_classes.items():
        cfg = settings.get(name, {})
        enabled = cfg.get("enabled", True)   # default enabled if not in settings

        if not enabled:
            logger.debug("Connector %s is disabled — skipping", name)
            continue

        url = urls.get(name)
        if not url:
            logger.debug("Connector %s: no URL provided — skipping", name)
            continue

        delay_s = cfg.get("delay_s", 1.0)
        try:
            connector = cls(delay_s=delay_s) if name != "manual_entry" else cls()
            kw = extra.get(name, {})
            record = connector.fetch(company_id, competitor_id, url, **kw)
            results.append(record)
            logger.info(
                "Connector %s: status=%s competitor=%s",
                name, record.status, competitor_id
            )
        except Exception as exc:
            logger.warning("Connector %s raised unexpectedly: %s", name, exc)
            results.append(ConnectorRecord(
                connector=name,
                company_id=company_id,
                competitor_id=competitor_id,
                source_url=url or "",
                status="unavailable",
                error_message=str(exc),
            ))

    return results

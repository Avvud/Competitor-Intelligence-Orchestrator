"""
manual_entry.py — import competitor data from a user-provided CSV or dict.

This connector is the fallback for any source that can't be automated
(LinkedIn, Instagram without API, Meta Ad Library manual exports, etc.)

CSV format:
  field,value
  name,Rival Corp
  website,https://rivalcorp.com
  employees,50-100
  ...

Bad rows (missing field or value) are rejected with line numbers logged.
"""

import csv
import io
import logging
from pathlib import Path

from src.connectors.base import BaseConnector, ConnectorRecord, content_hash

logger = logging.getLogger(__name__)


class ManualEntryConnector(BaseConnector):
    name = "manual_entry"

    def _fetch(
        self,
        company_id: str,
        competitor_id: str,
        url: str,           # file path or "manual:<label>"
        data: dict | None = None,
        csv_path: str | None = None,
        **kwargs,
    ) -> ConnectorRecord:
        """
        Accept either:
          - data dict: directly use it
          - csv_path: parse a key-value CSV file
        """
        if data:
            chash = content_hash(str(sorted(data.items())))
            return ConnectorRecord(
                connector=self.name,
                company_id=company_id,
                competitor_id=competitor_id,
                source_url=url,
                status="ok" if data else "empty",
                content_hash=chash,
                raw_data=data,
            )

        if csv_path:
            parsed, bad_rows = _parse_kv_csv(csv_path)
            if bad_rows:
                logger.warning(
                    "manual_entry CSV %s had %d bad rows: lines %s",
                    csv_path, len(bad_rows), bad_rows
                )
            chash = content_hash(str(sorted(parsed.items())))
            return ConnectorRecord(
                connector=self.name,
                company_id=company_id,
                competitor_id=competitor_id,
                source_url=csv_path,
                status="ok" if parsed else "empty",
                content_hash=chash,
                raw_data=parsed,
            )

        return ConnectorRecord(
            connector=self.name,
            company_id=company_id,
            competitor_id=competitor_id,
            source_url=url,
            status="empty",
            error_message="No data or csv_path provided",
        )


def _parse_kv_csv(csv_path: str) -> tuple[dict, list[int]]:
    """
    Parse a key-value CSV file (columns: field, value).
    Returns (parsed_dict, bad_row_numbers).
    """
    parsed: dict[str, str] = {}
    bad_rows: list[int] = []

    try:
        text = Path(csv_path).read_text(encoding="utf-8-sig")
    except Exception as exc:
        logger.error("Cannot read manual CSV %s: %s", csv_path, exc)
        return {}, []

    reader = csv.DictReader(io.StringIO(text))
    for i, row in enumerate(reader, start=2):
        field = (row.get("field") or "").strip()
        value = (row.get("value") or "").strip()
        if not field or not value:
            bad_rows.append(i)
            logger.warning("manual_entry CSV row %d: missing field or value — skipped", i)
            continue
        parsed[field] = value

    return parsed, bad_rows

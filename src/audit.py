"""Append-only audit log writer.

This module is implemented for real in Phase 1 (unlike the verifier, which is
stubbed). Every consequential action in the workbench, an AI draft being
generated, a claim being verified, a human disposition being recorded, should
call log_event so there is a tamper-evident trail.

The log is a CSV at data/audit_log.csv. Writes are append-only: we never edit
or delete existing rows, we only add new ones. The file can be tailed to watch
actions in real time:

    tail -f data/audit_log.csv
"""

from __future__ import annotations

import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Resolve data/ relative to the project root (the parent of src/), so the log
# lands in the same place regardless of the caller's working directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUDIT_LOG_PATH = PROJECT_ROOT / "data" / "audit_log.csv"

# Column order is fixed and matches src.schema.AuditLog.
FIELDNAMES = ["log_id", "timestamp", "actor", "action", "alert_id", "details_json"]


def log_event(
    actor: str,
    action: str,
    alert_id: str | None = None,
    details: dict[str, Any] | None = None,
    log_path: Path | None = None,
) -> dict[str, Any]:
    """Append one row to the audit log and return the row that was written.

    actor: who or what took the action, for example "ai_drafter" or "reviewer:jdoe".
    action: a short verb phrase, for example "claim_verified".
    alert_id: the alert the action relates to, if any.
    details: arbitrary structured context, serialized to JSON in the CSV.
    log_path: override the destination, used by tests to avoid touching real data.

    The log_id and timestamp are generated here so callers cannot forge them.
    """

    path = log_path or AUDIT_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    row = {
        "log_id": f"LOG-{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "action": action,
        "alert_id": alert_id or "",
        "details_json": json.dumps(details or {}, sort_keys=True),
    }

    # Write the header only when creating the file. Subsequent calls append.
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    return row

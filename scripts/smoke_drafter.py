"""Live smoke test. Run manually only. Requires ANTHROPIC_API_KEY in environment. Never runs in CI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.llm_drafter import draft_claims  # noqa: E402

DATA = ROOT / "data"
CUSTOMER_ID = "CUST0001"
ALERT_ID = "ALERT001"


def _load(table: str) -> pd.DataFrame:
    return pd.read_csv(DATA / f"{table}.csv", keep_default_na=False)


def main() -> int:
    # Load the alert row to send to the drafter.
    alerts = _load("alerts")
    alert_rows = alerts[alerts["alert_id"] == ALERT_ID]
    if len(alert_rows) == 0:
        print(f"FAILED: alert {ALERT_ID} not found in data/alerts.csv")
        return 1
    alert = alert_rows.iloc[0].to_dict()

    # Build source_tables filtered to this customer. evidence_items is keyed by
    # alert_id rather than customer_id, so it is filtered on the alert.
    source_tables = {
        "customers": _load("customers").query("customer_id == @CUSTOMER_ID"),
        "transactions": _load("transactions").query("customer_id == @CUSTOMER_ID"),
        "prior_cases": _load("prior_cases").query("customer_id == @CUSTOMER_ID"),
        "kyc_profile_status": _load("kyc_profile_status").query("customer_id == @CUSTOMER_ID"),
        "evidence_items": _load("evidence_items").query("alert_id == @ALERT_ID"),
    }

    print(f"Calling draft_claims against the live API for {ALERT_ID} ({CUSTOMER_ID}) ...")
    claims = draft_claims(alert, source_tables)

    if not claims:
        print("FAILED: draft_claims returned no claims (see data/audit_log.csv for the reason).")
        return 1

    print(f"OK: {len(claims)} claim(s) returned.\n")
    for i, claim in enumerate(claims, start=1):
        print(f"--- claim {i} ---")
        print(json.dumps(claim, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

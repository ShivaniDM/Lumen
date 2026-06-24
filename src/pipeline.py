"""End-to-end orchestration for one alert.

The flow embodies the build principle:
  Structured claims first. Deterministic verification second. Human approval last.

Step 1 (structured claims) supports two modes:
  Default: load the deterministic seeded claims from data/ai_outputs.csv. This
  is the default because the demo must not depend on network, API latency, or
  nondeterministic model output.
  Live: call llm_drafter.draft_claims, but only when explicitly enabled via the
  use_live_llm argument or the LUMEN_USE_LIVE_LLM=1 environment variable. Live
  mode never crashes the pipeline: on empty output or any failure it falls back
  to the seeded claims so the alert still gets processed.

Step 2 (verification) and Step 3 (human approval handoff) are unchanged.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from . import audit, llm_drafter, verifier

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA = PROJECT_ROOT / "data"

# Tables the verifier reads (passed in full; the verifier filters internally).
SOURCE_TABLE_NAMES = [
    "customers", "transactions", "prior_cases",
    "kyc_profile_status", "evidence_items", "alerts",
]


def _read_table(name: str) -> pd.DataFrame:
    """Load one data table as strings, matching the verifier/drafter contract."""
    return pd.read_csv(DATA / f"{name}.csv", keep_default_na=False, dtype=str)


def _load_source_tables() -> dict[str, pd.DataFrame]:
    """Full source tables for the verifier loop."""
    return {name: _read_table(name) for name in SOURCE_TABLE_NAMES}


def _load_seeded_claims(alert_id: str) -> list[dict[str, Any]]:
    """Deterministic claims for an alert, parsed from data/ai_outputs.csv.

    Returns the same list[dict] shape the verifier loop expects, with
    evidence_refs parsed from its JSON string back into a Python list.
    """
    try:
        ai = _read_table("ai_outputs")
    except FileNotFoundError:
        return []
    rows = ai[ai["alert_id"] == alert_id]
    claims: list[dict[str, Any]] = []
    for _, r in rows.iterrows():
        try:
            refs = json.loads(r["evidence_refs"]) if r["evidence_refs"] else []
        except (ValueError, TypeError):
            refs = []
        claims.append(
            {
                "output_id": r["output_id"],
                "alert_id": r["alert_id"],
                "claim_id": r["claim_id"],
                "claim_type": r["claim_type"],
                "asserted_value": r["asserted_value"],
                "evidence_refs": refs,
                "generated_at": r["generated_at"],
            }
        )
    return claims


def _build_live_inputs(alert_id: str):
    """Build (alert dict, filtered source_tables) for llm_drafter.draft_claims.

    Returns (None, None) if the alert_id is not found. source_tables is filtered
    to the alert's customer (evidence_items is filtered by alert_id), per the
    drafter contract.
    """
    alerts_df = _read_table("alerts")
    arow = alerts_df[alerts_df["alert_id"] == alert_id]
    if len(arow) == 0:
        return None, None
    arow = arow.iloc[0]
    alert = {
        "alert_id": arow["alert_id"],
        "customer_id": arow["customer_id"],
        "rule_triggered": arow["rule_triggered"],
        "severity": arow["severity"],
        "triggered_at": arow["triggered_at"],
        "status": arow["status"],
    }
    cid = arow["customer_id"]
    customers = _read_table("customers")
    transactions = _read_table("transactions")
    prior_cases = _read_table("prior_cases")
    kyc = _read_table("kyc_profile_status")
    evidence = _read_table("evidence_items")
    source_tables = {
        "customers": customers[customers["customer_id"] == cid],
        "transactions": transactions[transactions["customer_id"] == cid],
        "prior_cases": prior_cases[prior_cases["customer_id"] == cid],
        "kyc_profile_status": kyc[kyc["customer_id"] == cid],
        "evidence_items": evidence[evidence["alert_id"] == alert_id],
    }
    return alert, source_tables


def process_alert(
    alert_id: str,
    source: Any = None,
    use_live_llm: bool = False,
) -> dict[str, Any]:
    """Run one alert through the pipeline.

    alert_id: the alert to process.
    source: full source tables for verification. Loaded from data/ if not given.
    use_live_llm: when True (or env LUMEN_USE_LIVE_LLM=1), draft claims with the
        live LLM instead of the seeded data. Defaults to False (demo-safe).
    """
    live = use_live_llm or os.getenv("LUMEN_USE_LIVE_LLM") == "1"

    audit.log_event(actor="pipeline", action="alert_processing_started", alert_id=alert_id)

    # The verifier needs the full source tables. Load them if the caller did not
    # supply them, so verification is real rather than defaulting to NEEDS_REVIEW.
    if source is None:
        source = _load_source_tables()

    # Step 1: STRUCTURED CLAIMS FIRST.
    if live:
        try:
            alert_row, drafter_source = _build_live_inputs(alert_id)
            drafted = (
                llm_drafter.draft_claims(alert_row, drafter_source)
                if alert_row is not None
                else []
            )
            if drafted:
                claims = drafted
            else:
                # Live mode returned nothing usable. Fall back to seeded claims.
                audit.log_event(actor="pipeline", action="draft_empty", alert_id=alert_id)
                claims = _load_seeded_claims(alert_id)
        except Exception as exc:  # never crash the pipeline on a drafter or data failure
            audit.log_event(
                actor="pipeline",
                action="draft_failed",
                alert_id=alert_id,
                details={"reason": f"{type(exc).__name__}: {exc}"},
            )
            claims = _load_seeded_claims(alert_id)
    else:
        # Default deterministic path: seeded claims, no network, no LLM.
        claims = _load_seeded_claims(alert_id)

    # Step 2: DETERMINISTIC VERIFICATION SECOND.
    # Each claim is checked against the source data by the deterministic verifier.
    results = []
    for claim in claims:
        result = verifier.verify_claim(claim, source)
        results.append(result)
        audit.log_event(
            actor="verifier",
            action="claim_verified",
            alert_id=alert_id,
            details={
                "claim_id": claim.get("claim_id"),
                "claim_type": claim.get("claim_type"),
                "status": result.status,
                "reason": result.reason,
            },
        )

    # Step 3: HUMAN APPROVAL LAST.
    # TODO (UI, teammate): present claims and verification results to a reviewer,
    # require a disposition with decision_reason and final_note, then persist a
    # human_reviews row. The rubber-stamp gate (see HERO CASE B) lives here: a
    # disposition missing required fields must be blocked.
    disposition = None  # TODO: collected from the reviewer via the UI.

    audit.log_event(
        actor="pipeline",
        action="alert_processing_finished",
        alert_id=alert_id,
        details={"claim_count": len(claims), "disposition": disposition},
    )

    return {"alert_id": alert_id, "results": results, "disposition": disposition}

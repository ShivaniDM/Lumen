"""Claim verification spine.

This is the deterministic heart of the system: it takes a structured claim
emitted by the AI and checks it against the source data, returning PASS, FAIL,
or NEEDS_REVIEW. No LLM is involved in verification. The whole point is that the
check is deterministic and auditable.

The schema validation gate at the top of verify_claim rejects unknown claim
types and claims missing required evidence_refs. Below the gate, each claim type
has its own verifier function implementing the rule from docs/claim_types.md.

Source data contract: each verifier receives `source`, a dict of pandas
DataFrames keyed by table name (customers, transactions, prior_cases,
kyc_profile_status, evidence_items, alerts). All values are strings (CSV loaded
with dtype=str), so numeric and boolean fields are cast explicitly. A claim's
customer is resolved by looking up its alert_id in the alerts table. No verifier
ever raises: missing tables, missing rows, or bad casts return NEEDS_REVIEW.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

import pandas as pd

# The closed claim-type vocabulary. Must stay in sync with docs/claim_types.md.
# A claim_type not in this set is rejected by the validation gate.
KNOWN_CLAIM_TYPES: frozenset[str] = frozenset(
    {
        "prior_sar_history",
        "rapid_movement",
        "structuring",
        "expected_activity_mismatch",
        "missing_kyc_data",
        "stale_kyc_profile",
        "high_risk_country",
        "unusual_transaction_volume",
        "prior_alert_history",
    }
)

# Claim types whose verification cannot proceed without evidence_refs pointing
# at the source rows to check. The gate rejects these if evidence_refs is empty.
REQUIRES_EVIDENCE_REFS: frozenset[str] = frozenset(
    {
        "prior_sar_history",
        "rapid_movement",
        "structuring",
        "expected_activity_mismatch",
        "high_risk_country",
        "unusual_transaction_volume",
        "prior_alert_history",
    }
)

# Counterparty country codes treated as high risk. ISO alpha-2 two-letter codes,
# canonical list mirrored in docs/claim_types.md (high_risk_country section).
HIGH_RISK_COUNTRIES: frozenset[str] = frozenset(
    ["IR", "KP", "SY", "MM", "CU", "VE", "RU"]
)

VerificationStatus = Literal["PASS", "FAIL", "NEEDS_REVIEW"]


@dataclass(frozen=True)
class VerificationResult:
    """The outcome of verifying one claim.

    status: PASS (claim confirmed by source), FAIL (claim contradicted by
    source), or NEEDS_REVIEW (cannot decide deterministically, route to a human).
    reason: a short human-readable explanation, shown in the UI and logged.
    claim_type: echoed back for convenience when results are collected in bulk.
    """

    status: VerificationStatus
    reason: str
    claim_type: str | None = None


# --------------------------------------------------------------------------
# Internal helpers. These keep the per-type verifiers short and uniform, and
# centralize the "never raise, return NEEDS_REVIEW" error handling.
# --------------------------------------------------------------------------

def _result(status: VerificationStatus, reason: str, claim: dict[str, Any]) -> VerificationResult:
    return VerificationResult(status=status, reason=reason, claim_type=claim.get("claim_type"))


def _get_table(source: Any, name: str, claim: dict[str, Any]):
    """Return (DataFrame, None) or (None, NEEDS_REVIEW result) if unavailable."""
    if not isinstance(source, dict):
        return None, _result("NEEDS_REVIEW", "source data is missing or not a dict", claim)
    df = source.get(name)
    if df is None:
        return None, _result("NEEDS_REVIEW", f"required table '{name}' is absent from source", claim)
    return df, None


def _resolve_customer_id(claim: dict[str, Any], source: Any):
    """Resolve the claim's customer_id via its alert_id in the alerts table.

    Returns (customer_id, None) or (None, NEEDS_REVIEW result) on any failure.
    """
    alerts_df, err = _get_table(source, "alerts", claim)
    if err is not None:
        return None, err
    alert_id = claim.get("alert_id")
    row = alerts_df[alerts_df["alert_id"] == alert_id]
    if len(row) == 0:
        return None, _result("NEEDS_REVIEW", f"could not resolve customer for alert_id {alert_id!r}", claim)
    return row.iloc[0]["customer_id"], None


def _expected_and_amounts(claim: dict[str, Any], source: Any):
    """Shared loader for the two volume claims.

    Returns (expected_monthly_volume, list_of_amounts, None) or
    (None, None, NEEDS_REVIEW result) on any failure.
    """
    customers, err = _get_table(source, "customers", claim)
    if err is not None:
        return None, None, err
    txns, err = _get_table(source, "transactions", claim)
    if err is not None:
        return None, None, err
    cid, err = _resolve_customer_id(claim, source)
    if err is not None:
        return None, None, err

    crow = customers[customers["customer_id"] == cid]
    if len(crow) == 0:
        return None, None, _result("NEEDS_REVIEW", f"no customers row for {cid}", claim)
    try:
        expected = float(crow.iloc[0]["expected_monthly_volume"])
    except (ValueError, TypeError):
        return None, None, _result("NEEDS_REVIEW", "could not parse expected_monthly_volume", claim)

    trows = txns[txns["customer_id"] == cid]
    try:
        amounts = [float(a) for a in trows["amount"]]
    except (ValueError, TypeError):
        return None, None, _result("NEEDS_REVIEW", "could not parse a transaction amount", claim)
    return expected, amounts, None


# --------------------------------------------------------------------------
# Per-claim-type verifiers. Each implements the rule in docs/claim_types.md.
# Signatures are fixed by the dispatcher contract: (claim, source) -> result.
# --------------------------------------------------------------------------

def verify_prior_sar_history(claim: dict[str, Any], source: Any) -> VerificationResult:
    """PASS if prior_cases.prior_sar_count > 0 for the customer, else FAIL."""
    prior, err = _get_table(source, "prior_cases", claim)
    if err is not None:
        return err
    cid, err = _resolve_customer_id(claim, source)
    if err is not None:
        return err
    rows = prior[prior["customer_id"] == cid]
    if len(rows) == 0:
        return _result("NEEDS_REVIEW", f"no prior_cases row for customer {cid}", claim)
    try:
        count = int(rows.iloc[0]["prior_sar_count"])
    except (ValueError, TypeError):
        return _result("NEEDS_REVIEW", "could not parse prior_sar_count as an integer", claim)
    if count > 0:
        return _result("PASS", f"prior_sar_count={count} confirms prior SAR history for {cid}", claim)
    return _result("FAIL", f"prior_sar_count=0 for {cid}; asserted prior SAR history is contradicted", claim)


def verify_rapid_movement(claim: dict[str, Any], source: Any) -> VerificationResult:
    """PASS if 3+ transactions fall in a 24-hour window, include both an in and
    an out direction, and sum to more than 10000. Otherwise FAIL.
    """
    txns, err = _get_table(source, "transactions", claim)
    if err is not None:
        return err
    cid, err = _resolve_customer_id(claim, source)
    if err is not None:
        return err
    trows = txns[txns["customer_id"] == cid]
    if len(trows) == 0:
        return _result("FAIL", f"no transactions for {cid}; no rapid movement", claim)

    records = []
    try:
        for _, r in trows.iterrows():
            ts = pd.to_datetime(r["timestamp"])
            amount = float(r["amount"])
            direction = str(r["direction"]).strip().lower()
            records.append((ts, direction, amount))
    except (ValueError, TypeError):
        return _result("NEEDS_REVIEW", "could not parse a transaction timestamp or amount", claim)

    records.sort(key=lambda rec: rec[0])
    window = pd.Timedelta(hours=24)
    for start_ts, _, _ in records:
        win = [rec for rec in records if start_ts <= rec[0] <= start_ts + window]
        if len(win) >= 3:
            directions = {d for _, d, _ in win}
            total = sum(a for _, _, a in win)
            if "in" in directions and "out" in directions and total > 10000:
                return _result(
                    "PASS",
                    f"{len(win)} transactions within 24h totaling {total:.2f} with both in and out flows",
                    claim,
                )
    return _result("FAIL", "no 24-hour window with 3+ in/out transactions summing over 10000", claim)


def verify_structuring(claim: dict[str, Any], source: Any) -> VerificationResult:
    """PASS if 2+ deposits (direction in), each strictly between 9000 and 9999,
    fall within a 72-hour window. Otherwise FAIL.
    """
    txns, err = _get_table(source, "transactions", claim)
    if err is not None:
        return err
    cid, err = _resolve_customer_id(claim, source)
    if err is not None:
        return err
    trows = txns[txns["customer_id"] == cid]
    if len(trows) == 0:
        return _result("FAIL", f"no transactions for {cid}; no structuring", claim)

    deposits = []
    try:
        for _, r in trows.iterrows():
            direction = str(r["direction"]).strip().lower()
            if direction != "in":
                continue
            amount = float(r["amount"])
            if not (9000 < amount < 9999):
                continue
            deposits.append((pd.to_datetime(r["timestamp"]), amount))
    except (ValueError, TypeError):
        return _result("NEEDS_REVIEW", "could not parse a transaction timestamp or amount", claim)

    deposits.sort(key=lambda rec: rec[0])
    window = pd.Timedelta(hours=72)
    for start_ts, _ in deposits:
        win = [rec for rec in deposits if start_ts <= rec[0] <= start_ts + window]
        if len(win) >= 2:
            return _result(
                "PASS",
                f"{len(win)} sub-threshold deposits within 72h consistent with structuring",
                claim,
            )
    return _result("FAIL", "no 72-hour window with 2+ deposits strictly between 9000 and 9999", claim)


def verify_expected_activity_mismatch(claim: dict[str, Any], source: Any) -> VerificationResult:
    """PASS if any single transaction amount exceeds expected_monthly_volume."""
    expected, amounts, err = _expected_and_amounts(claim, source)
    if err is not None:
        return err
    if not amounts:
        return _result("FAIL", "no transactions to compare against expected volume", claim)
    biggest = max(amounts)
    if biggest > expected:
        return _result(
            "PASS",
            f"largest transaction {biggest:.2f} exceeds expected_monthly_volume {expected:.2f}",
            claim,
        )
    return _result(
        "FAIL",
        f"largest transaction {biggest:.2f} does not exceed expected_monthly_volume {expected:.2f}",
        claim,
    )


def verify_missing_kyc_data(claim: dict[str, Any], source: Any) -> VerificationResult:
    """PASS if any evidence_items row for this alert has available == false."""
    ev, err = _get_table(source, "evidence_items", claim)
    if err is not None:
        return err
    alert_id = claim.get("alert_id")
    rows = ev[ev["alert_id"] == alert_id]
    if len(rows) == 0:
        return _result("NEEDS_REVIEW", f"no evidence_items for alert {alert_id!r}", claim)
    available = [str(a).strip().lower() for a in rows["available"]]
    if "false" in available:
        return _result("PASS", "at least one required evidence item is unavailable", claim)
    return _result("FAIL", "all evidence items for this alert are available", claim)


def verify_stale_kyc_profile(claim: dict[str, Any], source: Any) -> VerificationResult:
    """PASS if kyc_profile_status.current_within_12mo is false, else FAIL.

    The CSV stores the boolean as a string. Comparison is case-insensitive so
    both "false" and "False" are handled.
    """
    kyc, err = _get_table(source, "kyc_profile_status", claim)
    if err is not None:
        return err
    cid, err = _resolve_customer_id(claim, source)
    if err is not None:
        return err
    rows = kyc[kyc["customer_id"] == cid]
    if len(rows) == 0:
        return _result("NEEDS_REVIEW", f"no kyc_profile_status row for {cid}", claim)
    value = str(rows.iloc[0]["current_within_12mo"]).strip().lower()
    if value == "false":
        return _result("PASS", f"KYC profile for {cid} is not current within 12 months", claim)
    if value == "true":
        return _result("FAIL", f"KYC profile for {cid} is current within 12 months", claim)
    return _result("NEEDS_REVIEW", f"unexpected current_within_12mo value {value!r}", claim)


def verify_high_risk_country(claim: dict[str, Any], source: Any) -> VerificationResult:
    """PASS if any of the customer's transactions has a high-risk counterparty."""
    txns, err = _get_table(source, "transactions", claim)
    if err is not None:
        return err
    cid, err = _resolve_customer_id(claim, source)
    if err is not None:
        return err
    trows = txns[txns["customer_id"] == cid]
    hits = sorted(
        {
            str(c).strip().upper()
            for c in trows["counterparty_country"]
            if str(c).strip().upper() in HIGH_RISK_COUNTRIES
        }
    )
    if hits:
        return _result("PASS", f"transactions to high-risk countries: {', '.join(hits)}", claim)
    return _result("FAIL", f"no transactions to a high-risk country for {cid}", claim)


def verify_unusual_transaction_volume(claim: dict[str, Any], source: Any) -> VerificationResult:
    """PASS if any single transaction amount exceeds 2x expected_monthly_volume."""
    expected, amounts, err = _expected_and_amounts(claim, source)
    if err is not None:
        return err
    if not amounts:
        return _result("FAIL", "no transactions to compare against expected volume", claim)
    threshold = 2 * expected
    biggest = max(amounts)
    if biggest > threshold:
        return _result(
            "PASS",
            f"largest transaction {biggest:.2f} exceeds 2x expected_monthly_volume ({threshold:.2f})",
            claim,
        )
    return _result(
        "FAIL",
        f"largest transaction {biggest:.2f} does not exceed 2x expected_monthly_volume ({threshold:.2f})",
        claim,
    )


def verify_prior_alert_history(claim: dict[str, Any], source: Any) -> VerificationResult:
    """PASS if the customer has more than one alert on record, else FAIL."""
    alerts_df, err = _get_table(source, "alerts", claim)
    if err is not None:
        return err
    cid, err = _resolve_customer_id(claim, source)
    if err is not None:
        return err
    count = len(alerts_df[alerts_df["customer_id"] == cid])
    if count > 1:
        return _result("PASS", f"customer {cid} has {count} alerts on record", claim)
    return _result("FAIL", f"customer {cid} has {count} alert(s), not more than 1", claim)


# Dispatch table: claim_type to verifier function.
_DISPATCH: dict[str, Callable[[dict[str, Any], Any], VerificationResult]] = {
    "prior_sar_history": verify_prior_sar_history,
    "rapid_movement": verify_rapid_movement,
    "structuring": verify_structuring,
    "expected_activity_mismatch": verify_expected_activity_mismatch,
    "missing_kyc_data": verify_missing_kyc_data,
    "stale_kyc_profile": verify_stale_kyc_profile,
    "high_risk_country": verify_high_risk_country,
    "unusual_transaction_volume": verify_unusual_transaction_volume,
    "prior_alert_history": verify_prior_alert_history,
}


def verify_claim(claim: dict[str, Any], source: Any = None) -> VerificationResult:
    """Verify one claim against the source data.

    claim: a dict shaped like a row of ai_outputs (claim_type, asserted_value,
    evidence_refs, ...).
    source: the source data the verifier reads from (loaded tables). Unused by
    the stubs, required by the real implementations.

    A schema validation gate runs first. It returns NEEDS_REVIEW (never raises)
    when:
    - claim_type is missing or not in the closed vocabulary, or
    - evidence_refs is required for this claim type but missing or empty.

    Returning NEEDS_REVIEW instead of crashing means a malformed AI output gets
    routed to a human rather than taking the system down.
    """

    claim_type = claim.get("claim_type")

    # Gate 1: known claim type.
    if claim_type not in KNOWN_CLAIM_TYPES:
        return VerificationResult(
            status="NEEDS_REVIEW",
            reason=f"Unknown claim_type {claim_type!r}; not in the closed vocabulary.",
            claim_type=claim_type,
        )

    # Gate 2: evidence present for types that require it.
    if claim_type in REQUIRES_EVIDENCE_REFS and not claim.get("evidence_refs"):
        return VerificationResult(
            status="NEEDS_REVIEW",
            reason=f"Claim type {claim_type!r} requires evidence_refs, but none were provided.",
            claim_type=claim_type,
        )

    # Dispatch to the per-type verifier.
    return _DISPATCH[claim_type](claim, source)

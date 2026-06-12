"""Tests for the real verification logic in src/verifier.py.

These load the real synthetic CSVs from data/ and exercise each verifier
against the planted scenarios. Pure deterministic checks, no LLM, no mocking.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))

from src.verifier import (  # noqa: E402
    verify_expected_activity_mismatch,
    verify_high_risk_country,
    verify_prior_alert_history,
    verify_prior_sar_history,
    verify_rapid_movement,
    verify_stale_kyc_profile,
    verify_structuring,
)

SOURCE_TABLES = [
    "customers",
    "transactions",
    "prior_cases",
    "kyc_profile_status",
    "evidence_items",
    "alerts",
]


def load_source() -> dict[str, pd.DataFrame]:
    """Load every source table as strings, mirroring the production contract."""
    return {
        name: pd.read_csv(DATA / f"{name}.csv", keep_default_na=False, dtype=str)
        for name in SOURCE_TABLES
    }


def claim(claim_type: str, alert_id: str) -> dict:
    return {"claim_type": claim_type, "alert_id": alert_id, "claim_id": "TEST", "evidence_refs": ["x"]}


# Test 1: HERO CASE A. CUST0001 has prior_sar_count=0, AI asserted true -> FAIL.
def test_prior_sar_history_fail_for_cust0001():
    result = verify_prior_sar_history(claim("prior_sar_history", "ALERT001"), load_source())
    assert result.status == "FAIL", result.reason


# Test 2: CUST0007 has prior_sar_count=2 -> PASS.
def test_prior_sar_history_pass_for_cust0007():
    result = verify_prior_sar_history(claim("prior_sar_history", "ALERT007"), load_source())
    assert result.status == "PASS", result.reason


# Test 3: CUST0006 has current_within_12mo=False -> PASS.
def test_stale_kyc_profile_pass_for_cust0006():
    result = verify_stale_kyc_profile(claim("stale_kyc_profile", "ALERT006"), load_source())
    assert result.status == "PASS", result.reason


# Test 4: CUST0005 has a transaction to IR -> PASS.
def test_high_risk_country_pass_for_cust0005():
    result = verify_high_risk_country(claim("high_risk_country", "ALERT005"), load_source())
    assert result.status == "PASS", result.reason


# Test 5: CUST0003 has 4 same-amount same-day in/out transactions -> PASS.
def test_rapid_movement_pass_for_cust0003():
    result = verify_rapid_movement(claim("rapid_movement", "ALERT003"), load_source())
    assert result.status == "PASS", result.reason


# Test 6: CUST0004 has 4 sub-10000 deposits across 3 days -> PASS.
def test_structuring_pass_for_cust0004():
    result = verify_structuring(claim("structuring", "ALERT004"), load_source())
    assert result.status == "PASS", result.reason


# Test 7: CUST0002 has a 45000 inflow vs 2000 expected -> PASS.
def test_expected_activity_mismatch_pass_for_cust0002():
    result = verify_expected_activity_mismatch(claim("expected_activity_mismatch", "ALERT002"), load_source())
    assert result.status == "PASS", result.reason


# Test 8: missing source returns NEEDS_REVIEW, never raises.
def test_missing_source_returns_needs_review():
    result = verify_prior_sar_history(claim("prior_sar_history", "ALERT001"), None)
    assert result.status == "NEEDS_REVIEW", result.reason


# Test 9: CUST0030 has 2 alerts (ALERT038, ALERT039) -> PASS.
def test_prior_alert_history_pass_for_cust0030():
    result = verify_prior_alert_history(claim("prior_alert_history", "ALERT038"), load_source())
    assert result.status == "PASS", result.reason


# Test 10: a source dict missing the required table returns NEEDS_REVIEW.
def test_missing_table_returns_needs_review_with_reason():
    source = load_source()
    del source["prior_cases"]
    result = verify_prior_sar_history(claim("prior_sar_history", "ALERT001"), source)
    assert result.status == "NEEDS_REVIEW", result.reason
    assert "prior_cases" in result.reason.lower(), result.reason


# Test 11: an alert_id absent from alerts.csv cannot resolve a customer.
def test_unresolvable_alert_id_returns_needs_review():
    result = verify_prior_sar_history(claim("prior_sar_history", "ALERT999"), load_source())
    assert result.status == "NEEDS_REVIEW", result.reason


# Test 12: an empty source dict has no tables, so verification cannot proceed.
def test_empty_source_dict_returns_needs_review():
    result = verify_high_risk_country(claim("high_risk_country", "ALERT005"), {})
    assert result.status == "NEEDS_REVIEW", result.reason

"""Tests for src/llm_drafter.draft_claims.

These never call the real Anthropic API. The client is mocked. We verify the
shape of the output, the schema-enforcement drops, the never-raise fallback,
and the audit logging.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pandas as pd
import pytest

import anthropic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.llm_drafter import TOOL_NAME, draft_claims  # noqa: E402

REQUIRED_FIELDS = [
    "output_id",
    "alert_id",
    "claim_id",
    "claim_type",
    "asserted_value",
    "evidence_refs",
    "generated_at",
]

ALERT = {
    "alert_id": "ALERT001",
    "customer_id": "CUST0001",
    "rule_triggered": "R-LARGE-WIRE",
    "severity": "high",
    "triggered_at": "2026-05-20T09:15:00",
    "status": "in_review",
}


def _source_tables() -> dict[str, pd.DataFrame]:
    """Minimal source rows for CUST0001, enough to build the user message."""
    return {
        "customers": pd.DataFrame([{
            "customer_id": "CUST0001", "name": "Dana Whitfield", "kyc_status": "verified",
            "expected_monthly_volume": 12000.0, "country": "US", "occupation": "Consultant",
            "kyc_last_updated": "2025-09-01",
        }]),
        "transactions": pd.DataFrame([{
            "txn_id": "TXN00001", "customer_id": "CUST0001", "amount": 28000.0,
            "direction": "in", "counterparty_country": "GB", "timestamp": "2026-05-20T09:10:00",
        }]),
        "prior_cases": pd.DataFrame([{
            "case_id": "PC0001", "customer_id": "CUST0001", "prior_sar_count": 0, "last_sar_date": "",
        }]),
        "kyc_profile_status": pd.DataFrame([{
            "customer_id": "CUST0001", "current_within_12mo": True, "last_updated": "2025-09-01",
        }]),
        "evidence_items": pd.DataFrame([{
            "alert_id": "ALERT001", "item_type": "source_of_funds", "available": True,
            "last_checked": "2026-05-21T10:00:00",
        }]),
    }


def _tool_response(claims: list[dict]) -> SimpleNamespace:
    """Fake Messages response with one submit_aml_claims tool_use block."""
    block = SimpleNamespace(type="tool_use", name=TOOL_NAME, input={"claims": claims})
    return SimpleNamespace(content=[block])


def _patched_client(response=None, side_effect=None) -> MagicMock:
    """A mock anthropic.Anthropic() whose messages.create is controlled."""
    client = MagicMock()
    if side_effect is not None:
        client.messages.create.side_effect = side_effect
    else:
        client.messages.create.return_value = response
    return client


def _read_log_actions(log_path: Path) -> list[str]:
    df = pd.read_csv(log_path, keep_default_na=False)
    return list(df["action"])


# --------------------------------------------------------------------------
# Test 1: a valid tool response produces correctly shaped output dicts.
# --------------------------------------------------------------------------

def test_valid_claim_produces_well_shaped_output():
    response = _tool_response([
        {
            "claim_type": "prior_sar_history",
            "asserted_value": "true",
            "evidence_refs": ["prior_cases.customer_id=CUST0001"],
        }
    ])
    workdir = Path(tempfile.mkdtemp(prefix="drafter_t1_"))
    try:
        with patch("src.llm_drafter.anthropic.Anthropic", return_value=_patched_client(response=response)):
            out = draft_claims(ALERT, _source_tables(), log_path=workdir / "audit_log.csv")

        assert len(out) == 1
        claim = out[0]
        for field in REQUIRED_FIELDS:
            assert field in claim, f"missing field {field}"
        assert claim["claim_type"] == "prior_sar_history"
        assert claim["asserted_value"] == "true"
        assert claim["alert_id"] == "ALERT001"
        assert claim["output_id"].startswith("OUT-")
        assert claim["claim_id"].startswith("CLM-")
        # evidence_refs must be a list, not a JSON string.
        assert isinstance(claim["evidence_refs"], list)
        assert claim["evidence_refs"] == ["prior_cases.customer_id=CUST0001"]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# Test 2: an unknown claim type is dropped.
# --------------------------------------------------------------------------

def test_unknown_claim_type_is_dropped():
    response = _tool_response([
        {
            "claim_type": "invented_type",
            "asserted_value": "true",
            "evidence_refs": ["prior_cases.customer_id=CUST0001"],
        }
    ])
    workdir = Path(tempfile.mkdtemp(prefix="drafter_t2_"))
    try:
        with patch("src.llm_drafter.anthropic.Anthropic", return_value=_patched_client(response=response)):
            out = draft_claims(ALERT, _source_tables(), log_path=workdir / "audit_log.csv")
        assert out == []
        actions = _read_log_actions(workdir / "audit_log.csv")
        assert "claim_dropped" in actions
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# Test 3: missing evidence_refs on a type that requires it is dropped.
# --------------------------------------------------------------------------

def test_missing_evidence_refs_is_dropped():
    response = _tool_response([
        {
            "claim_type": "prior_sar_history",
            "asserted_value": "true",
            "evidence_refs": [],
        }
    ])
    workdir = Path(tempfile.mkdtemp(prefix="drafter_t3_"))
    try:
        with patch("src.llm_drafter.anthropic.Anthropic", return_value=_patched_client(response=response)):
            out = draft_claims(ALERT, _source_tables(), log_path=workdir / "audit_log.csv")
        assert out == []
        actions = _read_log_actions(workdir / "audit_log.csv")
        assert "claim_dropped" in actions
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# Test 4: an API failure returns an empty list without raising.
# --------------------------------------------------------------------------

def test_api_failure_returns_empty_list():
    err = anthropic.APIError(
        "boom", httpx.Request("POST", "https://api.anthropic.com/v1/messages"), body=None
    )
    workdir = Path(tempfile.mkdtemp(prefix="drafter_t4_"))
    try:
        with patch("src.llm_drafter.anthropic.Anthropic", return_value=_patched_client(side_effect=err)):
            out = draft_claims(ALERT, _source_tables(), log_path=workdir / "audit_log.csv")
        assert out == []
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# Test 5: the audit log records draft_started and draft_completed.
# --------------------------------------------------------------------------

def test_audit_log_records_start_and_complete():
    response = _tool_response([
        {
            "claim_type": "prior_sar_history",
            "asserted_value": "true",
            "evidence_refs": ["prior_cases.customer_id=CUST0001"],
        }
    ])
    workdir = Path(tempfile.mkdtemp(prefix="drafter_t5_"))
    log_path = workdir / "audit_log.csv"
    try:
        with patch("src.llm_drafter.anthropic.Anthropic", return_value=_patched_client(response=response)):
            draft_claims(ALERT, _source_tables(), log_path=log_path)
        actions = _read_log_actions(log_path)
        assert "draft_started" in actions
        assert "draft_completed" in actions
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

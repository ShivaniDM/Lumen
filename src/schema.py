"""Pydantic v2 data schema for the Defensible AML Decision Workbench.

Each class models one table. The CSV files in data/ are the on-disk
representation of these models, one CSV per table. These models are the single
source of truth for field names and types: the data generator, the verifier,
and the tests all read against them.

Design notes:
- Enumerated fields use typing.Literal so an invalid value fails validation
  rather than silently passing.
- Timestamp fields use datetime, date-only fields use date. Pydantic v2 coerces
  ISO 8601 strings (as stored in the CSVs) into these types automatically.
- evidence_refs (ai_outputs) and details_json (audit_log) hold structured data.
  In the CSVs they are stored as JSON strings. In these models they are typed
  as native Python structures. Use json.loads / json.dumps at the CSV boundary.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# Closed enumerations. These are the only legal values for their fields.
KycStatus = Literal["verified", "pending", "expired"]
Direction = Literal["in", "out"]
Severity = Literal["high", "med", "low"]
AlertStatus = Literal["open", "in_review", "closed"]
DraftDisposition = Literal["accepted", "edited", "rejected"]


class Customer(BaseModel):
    """A bank customer (the account holder under review)."""

    customer_id: str
    name: str
    kyc_status: KycStatus
    expected_monthly_volume: float = Field(
        ..., description="Declared or modeled expected throughput, in account currency."
    )
    country: str
    occupation: str
    kyc_last_updated: date


class Transaction(BaseModel):
    """A single money movement on a customer account."""

    txn_id: str
    customer_id: str
    amount: float
    direction: Direction
    counterparty_country: str
    timestamp: datetime


class Alert(BaseModel):
    """A monitoring alert raised against a customer by a detection rule."""

    alert_id: str
    customer_id: str
    rule_triggered: str
    severity: Severity
    triggered_at: datetime
    status: AlertStatus


class EvidenceItem(BaseModel):
    """One piece of evidence an analyst would expect to find for an alert.

    available indicates whether the item is actually present and retrievable.
    """

    alert_id: str
    item_type: str
    available: bool
    last_checked: datetime


class PriorCase(BaseModel):
    """A customer's historical SAR (suspicious activity report) record.

    prior_sar_count is the count of SARs previously filed for this customer.
    It is the ground truth that HERO CASE A's AI draft contradicts.
    """

    case_id: str
    customer_id: str
    prior_sar_count: int
    last_sar_date: date | None = None


class KycProfileStatus(BaseModel):
    """Derived KYC currency flag for a customer.

    current_within_12mo is True when the KYC profile was refreshed in the last
    12 months. This is the field a stale_kyc_profile claim is verified against.
    """

    customer_id: str
    current_within_12mo: bool
    last_updated: date


class AiOutput(BaseModel):
    """One structured claim emitted by the AI for an alert.

    The AI never writes free text into the decision record. It emits claims
    drawn from the closed vocabulary in docs/claim_types.md. asserted_value is
    the value the AI asserts (for example "true" for a boolean claim, or a
    numeric string). evidence_refs points at the source rows the AI says
    support the claim.

    In Phase 1 these rows are hardcoded, not LLM generated.
    """

    output_id: str
    alert_id: str
    claim_id: str
    claim_type: str
    asserted_value: str
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="References to source rows, for example ['prior_cases.case_id=PC0007'].",
    )
    generated_at: datetime


class HumanReview(BaseModel):
    """A human reviewer's disposition of an alert.

    draft_disposition records what the reviewer did with the AI draft.
    decision_reason and final_note are required for an auditable decision: a
    review missing them is a rubber stamp (see HERO CASE B). They are optional
    in the model so that the incomplete hero case can be loaded and detected,
    rather than failing to parse.
    """

    review_id: str
    alert_id: str
    reviewer: str
    evidence_reviewed: bool
    draft_disposition: DraftDisposition
    decision_reason: str | None = None
    final_note: str | None = None
    final_action: str | None = None
    reviewed_at: datetime


class AuditLog(BaseModel):
    """An append-only record of an action taken in the workbench.

    details_json holds arbitrary structured context for the action. In the CSV
    it is a JSON string.
    """

    log_id: str
    timestamp: datetime
    actor: str
    action: str
    alert_id: str | None = None
    details_json: dict[str, Any] = Field(default_factory=dict)


# Registry mapping table name to model and CSV filename. Used by loaders,
# generators, and tests so the table set is defined in exactly one place.
TABLES: dict[str, type[BaseModel]] = {
    "customers": Customer,
    "transactions": Transaction,
    "alerts": Alert,
    "evidence_items": EvidenceItem,
    "prior_cases": PriorCase,
    "kyc_profile_status": KycProfileStatus,
    "ai_outputs": AiOutput,
    "human_reviews": HumanReview,
    "audit_log": AuditLog,
}

CSV_FILES: dict[str, str] = {name: f"{name}.csv" for name in TABLES}

"""Claim verification spine (STUB).

This is the deterministic heart of the system: it takes a structured claim
emitted by the AI and checks it against the source data, returning PASS, FAIL,
or NEEDS_REVIEW. No LLM is involved in verification. The whole point is that the
check is deterministic and auditable.

Phase 1 status: STUB. The schema validation gate at the top of verify_claim is
real and works now. The per-claim-type verification functions are stubs that
all return NEEDS_REVIEW with a TODO. Real pattern-matching and field-comparison
logic lands in weeks 5 to 6.

The dispatcher pattern below is the contract the real implementations must
honor: one function per claim type, each taking (claim, source) and returning a
VerificationResult.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

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


def _needs_review_stub(claim: dict[str, Any], source: Any) -> VerificationResult:
    """Placeholder used by every per-type verifier until real logic exists."""
    # TODO (weeks 5 to 6): implement deterministic verification for this claim
    # type. Compare claim["asserted_value"] against the source rows named in
    # claim["evidence_refs"] and return PASS or FAIL.
    return VerificationResult(
        status="NEEDS_REVIEW",
        reason="Verifier not yet implemented for this claim type (Phase 1 stub).",
        claim_type=claim.get("claim_type"),
    )


# Per-claim-type verifiers. All stubs for now. Real implementations replace the
# bodies, not the signatures.
def verify_prior_sar_history(claim: dict[str, Any], source: Any) -> VerificationResult:
    # TODO: compare asserted prior_sar_history against prior_cases.prior_sar_count.
    return _needs_review_stub(claim, source)


def verify_rapid_movement(claim: dict[str, Any], source: Any) -> VerificationResult:
    # TODO: detect funds in then out within a short window in transactions.
    return _needs_review_stub(claim, source)


def verify_structuring(claim: dict[str, Any], source: Any) -> VerificationResult:
    # TODO: detect multiple sub-threshold deposits aggregating over a window.
    return _needs_review_stub(claim, source)


def verify_expected_activity_mismatch(claim: dict[str, Any], source: Any) -> VerificationResult:
    # TODO: compare transaction amounts against customers.expected_monthly_volume.
    return _needs_review_stub(claim, source)


def verify_missing_kyc_data(claim: dict[str, Any], source: Any) -> VerificationResult:
    # TODO: check evidence_items.available for required KYC items.
    return _needs_review_stub(claim, source)


def verify_stale_kyc_profile(claim: dict[str, Any], source: Any) -> VerificationResult:
    # TODO: check kyc_profile_status.current_within_12mo.
    return _needs_review_stub(claim, source)


def verify_high_risk_country(claim: dict[str, Any], source: Any) -> VerificationResult:
    # TODO: check counterparty_country against the high-risk country list.
    return _needs_review_stub(claim, source)


def verify_unusual_transaction_volume(claim: dict[str, Any], source: Any) -> VerificationResult:
    # TODO: compare aggregate volume against expected_monthly_volume baseline.
    return _needs_review_stub(claim, source)


def verify_prior_alert_history(claim: dict[str, Any], source: Any) -> VerificationResult:
    # TODO: count prior alerts for the customer in alerts.
    return _needs_review_stub(claim, source)


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

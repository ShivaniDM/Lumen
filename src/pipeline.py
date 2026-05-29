"""End-to-end orchestration outline (STUB).

This module shows the intended flow of a single alert through the workbench, as
a sequence of function calls. It is a skeleton: the layers that do not exist yet
are marked TODO. It exists so the team can see how the pieces connect before
they are all built.

The flow embodies the build principle:
  Structured claims first. Deterministic verification second. Human approval last.
"""

from __future__ import annotations

from typing import Any

from . import audit, verifier


def process_alert(alert_id: str, source: Any = None) -> dict[str, Any]:
    """Run one alert through the (eventual) full pipeline.

    Phase 1: only the verification dispatch and audit logging are wired. The
    AI drafting step and the human-review handoff are TODO.
    """

    audit.log_event(actor="pipeline", action="alert_processing_started", alert_id=alert_id)

    # Step 1: STRUCTURED CLAIMS FIRST.
    # TODO (LLM integration, later phase): call the AI drafter to produce
    # structured claims for this alert. For Phase 1 the claims already exist as
    # hardcoded rows in data/ai_outputs.csv, so this step is a no-op load.
    claims: list[dict[str, Any]] = []  # TODO: load ai_outputs rows for alert_id.

    # Step 2: DETERMINISTIC VERIFICATION SECOND.
    # Each claim is checked against the source data. This part is real (the
    # dispatcher), though the per-type logic is still stubbed.
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

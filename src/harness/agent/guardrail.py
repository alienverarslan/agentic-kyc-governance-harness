"""The deterministic guardrail: the component that DISPOSES.

Design commitment: the LLM proposes; this guardrail decides. The final decision is a
pure, auditable function of the finding SEVERITIES gathered across every check. The LLM
proposal never changes the outcome — when it disagrees with the policy the guardrail
wins and records an override, in BOTH directions:

* too lax  — LLM proposes approve while an unexplainable finding exists → escalate.
* too cautious — LLM proposes escalate on explainable-only findings → request_more_info.

Authority model (WHY these severities):

* Identity fields (tax_id, canonical legal_name): the Trade Registry (B1) is
  authoritative. An identity conflict admits no benign explanation, so it is
  ``unexplainable`` → escalate (there is no autonomous "reject" in this substrate; even
  suspected fraud is an escalation).
* Ownership / authority fields: the most recent document takes precedence, but any
  delta from an older document creates an EXPLANATION OBLIGATION. Whether that
  obligation is discharge-able is decided precisely by the checks (see
  ``harness.agent.checks``): a discharge-able delta is ``explainable`` →
  request_more_info; one that is not is ``unexplainable`` → escalate.
"""

from __future__ import annotations

from typing import Iterable

from harness.contracts.findings import (
    Decision,
    Finding,
    GuardrailDecision,
    Severity,
    SynthesisProposal,
)

# Severity precedence. Highest first: a single unexplainable finding dominates.
_UNEXPLAINABLE: Severity = "unexplainable"
_EXPLAINABLE: Severity = "explainable"

# Actions attached to each decision (the act node consumes these).
_ACTIONS: dict[Decision, list[str]] = {
    "escalate": ["open_case", "escalate"],
    "request_more_info": ["open_case"],
    "approve": [],
}


def decision_for_severities(severities: Iterable[str]) -> Decision:
    """The core policy, in strict precedence order.

    1. any ``unexplainable`` finding  → escalate
    2. else any ``explainable`` finding → request_more_info
    3. else (only info / no findings)   → approve
    """
    sev = set(severities)
    if _UNEXPLAINABLE in sev:
        return "escalate"
    if _EXPLAINABLE in sev:
        return "request_more_info"
    return "approve"


def apply_guardrail(
    findings: list[Finding], proposal: SynthesisProposal
) -> GuardrailDecision:
    """Compute the authoritative decision and reconcile it with the LLM proposal.

    The policy is a pure function of the finding severities, so it stands on its own when
    the synthesis call FAILED and there is no proposal at all. That case is recorded as
    ``finalization_mode="fail_closed_no_proposal"`` rather than as an override: there was
    nothing to override, and a bare ``overridden=False`` would be silently misreadable as
    "the LLM already agreed with the outcome". (The T0 system finding the failing node
    emitted is what drives ``final`` to escalate here.)
    """
    final = decision_for_severities(f.severity for f in findings)

    if proposal.status == "unavailable":
        return GuardrailDecision(
            final_decision=final,
            overridden=False,
            override_reason="",
            required_actions=list(_ACTIONS[final]),
            finalization_mode="fail_closed_no_proposal",
        )

    overridden = proposal.proposed_decision != final
    reason = ""
    if overridden:
        reason = (
            f"LLM proposed {proposal.proposed_decision!r} but policy requires "
            f"{final!r} given finding severities "
            f"{sorted({f.severity for f in findings}) or ['none']}."
        )
    return GuardrailDecision(
        final_decision=final,
        overridden=overridden,
        override_reason=reason,
        required_actions=list(_ACTIONS[final]),
        finalization_mode="proposal_guarded",
    )

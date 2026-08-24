"""The multi-step screening graph.

    extract -> resolve_entities
            -> check_identity_consistency
            -> check_ownership_consistency
            -> check_authority_chain
            -> check_ubo_derivation
            -> check_completeness
            -> check_learned_rules (Faz 3, human-promoted deterministic rules)
            -> ai_triage (LLM, open-world, fail-closed)
            -> synthesize (LLM) -> guardrail -> act -> END

Each check is a SEPARATE node. A check that cannot run because a required document is
absent sets ran=False, is recorded in ``skipped_checks``, and is NOT appended to the
trajectory — so a skipped check can never masquerade as "ran clean". The check nodes
still execute in graph order; only the trajectory/skip bookkeeping differs.
"""

from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph

from harness.agent import checks, learned_rules
from harness.agent.guardrail import apply_guardrail
from harness.agent.state import AgentState
from harness.contracts.documents import Dossier
from harness.contracts.findings import CaseRecord, CheckResult, ErrorKind, Finding
from harness.llm.errors import LLMError

logger = logging.getLogger(__name__)

# Bounded, user-safe renderings of each operational failure. Nothing is interpolated from
# the exception: a provider message can carry endpoints, response bodies, or prompt content.
_ERROR_DETAIL: dict[str, str] = {
    "timeout": "the model call timed out",
    "provider_error": "the model provider returned an error",
    "schema_invalid": "the model returned output that did not match the required schema",
    "unexpected_exception": "an unexpected error occurred while calling the model",
}


def _log_system_failure(
    node: str, kind: str, case_ref: str, exception_class: str, exc: BaseException | None = None
) -> None:
    """Internal observability with BOUNDED metadata only — no provider text at ERROR level.
    The full traceback is available at DEBUG, which an operator opts into deliberately."""
    logger.error(
        "system failure at %s",
        node,
        extra={
            "error_kind": kind,
            "exception_class": exception_class,
            "node": node,
            "case_ref": case_ref,
        },
    )
    if exc is not None:
        logger.debug("system failure traceback at %s (case_ref=%s)", node, case_ref, exc_info=exc)


def _system_finding(node: str, kind: ErrorKind) -> Finding:
    """A T0 finding for a failed LLM call. Fail-closed: unexplainable → escalate."""
    return Finding(
        check_name=node,
        code="T0",
        severity="unexplainable",
        detail=(
            f"{node}: {_ERROR_DETAIL[kind]}; this screening step could not complete, so "
            "the case is escalated for human review"
        ),
        fields_involved=[],
        origin="system",
        error_kind=kind,
    )


def _classify(exc: BaseException) -> ErrorKind:
    """An LLMError carries its own bounded kind; anything else is unexpected by definition
    (the client normalizes only KNOWN failures — see llm/errors.py)."""
    return exc.kind if isinstance(exc, LLMError) else "unexpected_exception"


def _ran(state: AgentState, node: str, result: CheckResult) -> dict:
    """Record a check that ran: append to trajectory + collect findings."""
    return {
        "trajectory": state.get("trajectory", []) + [node],
        "check_results": state.get("check_results", []) + [result],
        "findings": state.get("findings", []) + list(result.findings),
    }


def _skipped(state: AgentState, node: str) -> dict:
    """Record a check that could NOT run: skipped list only, never the trajectory."""
    return {
        "skipped_checks": state.get("skipped_checks", []) + [node],
        "check_results": state.get("check_results", [])
        + [CheckResult(check_name=node, ran=False, findings=[])],
    }


# --- nodes -------------------------------------------------------------------------
def extract(state: AgentState) -> dict:
    """Parse the dossier JSON into contract models (intentionally easy)."""
    dossier = Dossier.model_validate(state["dossier_raw"])
    return {"dossier": dossier, "trajectory": state.get("trajectory", []) + ["extract"]}


def resolve_entities(state: AgentState) -> dict:
    """Normalize all names (Turkish-aware) and build the cross-document entity map."""
    emap = checks.build_entity_map(state["dossier"])
    return {
        "emap": emap,
        "trajectory": state.get("trajectory", []) + ["resolve_entities"],
    }


def check_identity(state: AgentState) -> dict:
    node = "check_identity_consistency"
    dossier = state["dossier"]
    present = [d for d in (dossier.registry, dossier.circular, dossier.ubo) if d]
    if len(present) < 2:  # need at least two documents to compare identity
        return _skipped(state, node)
    return _ran(state, node, checks.check_identity_consistency(dossier, state["emap"]))


def check_ownership(state: AgentState) -> dict:
    node = "check_ownership_consistency"
    dossier = state["dossier"]
    if dossier.registry is None or dossier.ubo is None:
        return _skipped(state, node)
    return _ran(
        state,
        node,
        checks.check_ownership_consistency(dossier.registry, dossier.ubo, state["emap"]),
    )


def check_authority(state: AgentState) -> dict:
    node = "check_authority_chain"
    dossier = state["dossier"]
    if dossier.circular is None or dossier.ubo is None:
        return _skipped(state, node)
    return _ran(
        state,
        node,
        checks.check_authority_chain(dossier.circular, dossier.ubo, state["emap"]),
    )


def check_ubo(state: AgentState) -> dict:
    node = "check_ubo_derivation"
    dossier = state["dossier"]
    if dossier.ubo is None:  # cannot derive UBOs without the declaration
        return _skipped(state, node)
    return _ran(state, node, checks.check_ubo_derivation(dossier.ubo, state["emap"]))


def check_completeness_node(state: AgentState) -> dict:
    node = "check_completeness"
    return _ran(state, node, checks.check_completeness(state["dossier"]))


def check_learned_rules_node(state: AgentState) -> dict:
    """Faz 3: apply every currently-promoted rule. Always runs (no document
    precondition, like check_completeness); with zero promoted rules this is a no-op."""
    node = "check_learned_rules"
    return _ran(
        state,
        node,
        learned_rules.check_learned_rules(state["dossier"], state.get("promoted_rules", [])),
    )


def ai_triage(state: AgentState) -> dict:
    """AI open-world triage: the LLM flags red flags OUTSIDE deterministic coverage.

    Fail-closed in two distinct senses:
    * triage findings (X1/X2) only ever RAISE caution (severity clamped in
      ``triage_findings``); fed to the max-severity guardrail they can push a decision up
      but never down to approve;
    * if the CALL itself fails, that is an OPERATIONAL failure, not evidence about the
      company — it becomes a T0 (origin="system") finding, which escalates, rather than
      crashing the run or letting it continue as if triage had found nothing."""
    from harness.agent import coverage, triage
    from harness.contracts.findings import TriageResult

    node = "ai_triage"
    promoted = state.get("promoted_rules", [])
    user = triage.build_triage_user(state["dossier"], coverage.catalog_summary(promoted))
    try:
        result = state["llm"].complete_structured(triage.TRIAGE_SYSTEM, user, TriageResult)
    except Exception as exc:  # noqa: BLE001 - never BaseException; see _classify
        kind = _classify(exc)
        _log_system_failure(node, kind, state.get("case_ref", "unknown"), type(exc).__name__, exc)
        return {
            "findings": state.get("findings", []) + [_system_finding(node, kind)],
            "trajectory": state.get("trajectory", []) + [node],
        }

    findings = triage.triage_findings(result)
    return {
        "triage": result,
        "findings": state.get("findings", []) + findings,
        "trajectory": state.get("trajectory", []) + [node],
    }


def _synthesis_unavailable(state: AgentState, kind: ErrorKind) -> dict:
    """Record that NO usable proposal exists, plus the T0 that drives the escalation."""
    from harness.contracts.findings import NO_PROPOSAL_REASONING, SynthesisProposal

    return {
        "proposal": SynthesisProposal(
            status="unavailable",
            proposed_decision=None,
            reasoning=NO_PROPOSAL_REASONING,
            unavailable_reason="llm_call_failed",
        ),
        "findings": state.get("findings", []) + [_system_finding("synthesize", kind)],
        "trajectory": state.get("trajectory", []) + ["synthesize"],
    }


def synthesize(state: AgentState) -> dict:
    """The LLM decision proposal.

    If the call fails, the model produced NO proposal — so we record exactly that
    (``status="unavailable"``) rather than fabricating one. A sentinel
    ``proposed_decision="escalate"`` would read in the audit trail as "the LLM proposed
    escalate", which is false. The accompanying T0 finding is what drives the guardrail to
    escalate, and the guardrail records ``finalization_mode="fail_closed_no_proposal"``.

    A responding model may NOT declare its own unavailability. ``status`` is part of the
    schema the model is handed as a tool definition, so it *could* emit
    ``status="unavailable"`` — but unavailability is a fact the HARNESS observes when a call
    fails, never something a model that did answer may assert. Allowing it would let a model
    manufacture a "no proposal was available" audit record (and thereby dodge being recorded
    as overridden). It cannot affect the decision — findings alone drive that — but it would
    corrupt the audit trail, so it is rejected as out-of-contract output and handled
    fail-closed like any other invalid structured response."""
    from harness.contracts.findings import SynthesisProposal

    node = "synthesize"
    dossier = state["dossier"]
    results = state.get("check_results", [])
    user = checks.build_synthesis_user(dossier.dossier_id, results)

    try:
        proposal = state["llm"].complete_structured(
            checks.SYNTHESIS_SYSTEM, user, SynthesisProposal
        )
    except Exception as exc:  # noqa: BLE001 - never BaseException; see _classify
        kind = _classify(exc)
        _log_system_failure(node, kind, state.get("case_ref", "unknown"), type(exc).__name__, exc)
        return _synthesis_unavailable(state, kind)

    if proposal.status != "available":
        _log_system_failure(
            node, "schema_invalid", state.get("case_ref", "unknown"), "ModelDeclaredUnavailable"
        )
        return _synthesis_unavailable(state, "schema_invalid")

    return {
        "proposal": proposal,
        "trajectory": state.get("trajectory", []) + [node],
    }


def guardrail(state: AgentState) -> dict:
    """Deterministic policy; overrides the LLM proposal when they disagree."""
    decision = apply_guardrail(state.get("findings", []), state["proposal"])
    return {
        "guardrail": decision,
        "trajectory": state.get("trajectory", []) + ["guardrail"],
    }


def act(state: AgentState) -> dict:
    """Realize the decision as a side effect in the case store."""
    gd = state["guardrail"]
    record = CaseRecord(
        case_ref=state["case_ref"],
        decision=gd.final_decision,
        required_actions=gd.required_actions,
        escalated="escalate" in gd.required_actions,
    )
    store = state.get("store")
    if store is not None:
        store.write_record(record)
    return {"trajectory": state.get("trajectory", []) + ["act"]}


# --- assembly ----------------------------------------------------------------------
def build_graph():
    """Compile the screening graph once; callers invoke the returned app."""
    g = StateGraph(AgentState)
    g.add_node("extract", extract)
    g.add_node("resolve_entities", resolve_entities)
    g.add_node("check_identity_consistency", check_identity)
    g.add_node("check_ownership_consistency", check_ownership)
    g.add_node("check_authority_chain", check_authority)
    g.add_node("check_ubo_derivation", check_ubo)
    g.add_node("check_completeness", check_completeness_node)
    g.add_node("check_learned_rules", check_learned_rules_node)
    g.add_node("ai_triage", ai_triage)
    g.add_node("synthesize", synthesize)
    g.add_node("guardrail", guardrail)
    g.add_node("act", act)

    g.set_entry_point("extract")
    g.add_edge("extract", "resolve_entities")
    g.add_edge("resolve_entities", "check_identity_consistency")
    g.add_edge("check_identity_consistency", "check_ownership_consistency")
    g.add_edge("check_ownership_consistency", "check_authority_chain")
    g.add_edge("check_authority_chain", "check_ubo_derivation")
    g.add_edge("check_ubo_derivation", "check_completeness")
    g.add_edge("check_completeness", "check_learned_rules")
    g.add_edge("check_learned_rules", "ai_triage")
    g.add_edge("ai_triage", "synthesize")
    g.add_edge("synthesize", "guardrail")
    g.add_edge("guardrail", "act")
    g.add_edge("act", END)
    return g.compile()

"""Deterministic metrics.

Every metric is a categorical/set comparison — there is NO LLM-as-judge. The headline
safety metric, ``false_approval_rate``, is kept SEPARATE from aggregate accuracy: an
agent can look "mostly accurate" while still approving a dossier that should have been
escalated, and that failure mode must never be averaged away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.contracts.documents import Dossier
from harness.contracts.findings import AgentResult
from harness.contracts.truth import DecisionTruth

CHECK_NODES = {
    "check_identity_consistency",
    "check_ownership_consistency",
    "check_authority_chain",
    "check_ubo_derivation",
    "check_completeness",
}

# Cases whose ground truth is request_more_info form the "middle class". On the seed
# set these are #4, #6, #8 — both over-cautious (all→escalate) and lax (all→approve)
# agents fail here, which is exactly why it is reported separately.
MIDDLE_CLASS_DECISION = "request_more_info"


def agent_finding_codes(result: AgentResult) -> set[str]:
    """Set of DOMAIN taxonomy codes the agent produced; empty findings -> {"NONE"}.

    SYSTEM-origin findings (the T-family: a timed-out model call, a broken promoted rule)
    are excluded. They are operational failures, not evidence about the company, so folding
    them in would conflate "the harness broke" with "the agent found something" and would
    pollute finding accuracy with infrastructure noise. They are counted separately via
    ``AgentResult.system_failure_count`` / ``system_error_codes``.

    Note this does NOT weaken the safety metric: a T-finding is ``unexplainable``, and the
    guardrail still sees it, so it can only ever push a decision toward escalate.
    ``false_approval_rate`` therefore stays 0 structurally, not by assumption.
    """
    codes = {f.code for f in result.findings if f.origin != "system"}
    return codes or {"NONE"}


def finding_match(result: AgentResult, truth: DecisionTruth) -> bool:
    """Right decision for the RIGHT REASON: agent codes == injected codes (as sets)."""
    return agent_finding_codes(result) == set(truth.injected_codes)


def decision_match(result: AgentResult, truth: DecisionTruth) -> bool:
    return result.decision == truth.expected_decision


def trajectory_match(result: AgentResult, truth: DecisionTruth) -> bool:
    """Partial-order + skip checker.

    Requires: every expected ran-node present; every expected skip actually occurred; no
    skipped check appears in the trajectory (a skipped check reported as ran-clean is a
    failure); and the ordering extract < resolve < checks < synthesize < guardrail <
    act holds.

    The skip comparison is a SUBSET (expected ⊆ actual), not exact equality, so the
    matcher is forward-compatible with checks added after the fixtures were authored
    (e.g. check_ubo_derivation): a correct skip of such a check does not spuriously fail
    a fixture that predates it. This does NOT weaken failure detection for the fixtured
    checks — the presence check above still requires every expected-to-run check to have
    run, and the trajectory-membership check below still catches a skip reported as ran.
    """
    actual = result.trajectory
    if any(node not in actual for node in truth.expected_trajectory):
        return False
    if not set(truth.expected_skipped_checks) <= set(result.skipped_checks):
        return False
    if any(skipped in actual for skipped in result.skipped_checks):
        return False

    pos = {n: i for i, n in enumerate(actual)}
    if not {"extract", "resolve_entities", "synthesize", "guardrail", "act"} <= pos.keys():
        return False

    def before(x: str, y: str) -> bool:
        return pos[x] < pos[y]

    if not before("extract", "resolve_entities"):
        return False
    checks_present = [n for n in actual if n in CHECK_NODES]
    for c in checks_present:
        if not (before("resolve_entities", c) and before(c, "synthesize")):
            return False
    return before("synthesize", "guardrail") and before("guardrail", "act")


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    else:
        out[prefix] = value


def extraction_accuracy(agent_dossier: Dossier, truth_dossier: Dossier) -> float:
    """Field-level agreement between the agent's extract output and the loaded dossier.

    A cheap sanity metric (near-100% expected: the input is already structured JSON)."""
    a: dict[str, Any] = {}
    b: dict[str, Any] = {}
    _flatten("", agent_dossier.model_dump(mode="json"), a)
    _flatten("", truth_dossier.model_dump(mode="json"), b)
    keys = set(a) | set(b)
    if not keys:
        return 1.0
    matched = sum(1 for k in keys if a.get(k) == b.get(k))
    return matched / len(keys)


@dataclass
class CaseEvaluation:
    case_id: str
    injected_codes: list[str]
    expected_decision: str
    agent_decision: str
    decision_ok: bool
    finding_ok: bool
    agent_codes: list[str]
    trajectory_ok: bool
    extraction_acc: float
    overridden: bool
    expected_skipped: list[str]
    agent_skipped: list[str]
    system_failure_count: int = 0
    system_error_codes: list[str] = field(default_factory=list)


def evaluate_case(
    case_id: str, result: AgentResult, truth: DecisionTruth, agent_dossier: Dossier, truth_dossier: Dossier
) -> CaseEvaluation:
    return CaseEvaluation(
        case_id=case_id,
        injected_codes=list(truth.injected_codes),
        expected_decision=truth.expected_decision,
        agent_decision=result.decision,
        decision_ok=decision_match(result, truth),
        finding_ok=finding_match(result, truth),
        agent_codes=sorted(agent_finding_codes(result)),
        trajectory_ok=trajectory_match(result, truth),
        extraction_acc=extraction_accuracy(agent_dossier, truth_dossier),
        overridden=result.guardrail.overridden,
        expected_skipped=list(truth.expected_skipped_checks),
        agent_skipped=list(result.skipped_checks),
        system_failure_count=result.system_failure_count,
        system_error_codes=list(result.system_error_codes),
    )


def _acc(items: list[bool]) -> float:
    return sum(items) / len(items) if items else 0.0


def build_report(evals: list[CaseEvaluation]) -> dict[str, Any]:
    """Aggregate per-case evaluations into the stratified report."""
    n = len(evals)

    by_code: dict[str, dict[str, Any]] = {}
    for e in evals:
        key = "+".join(e.injected_codes)
        bucket = by_code.setdefault(key, {"correct": 0, "total": 0})
        bucket["total"] += 1
        bucket["correct"] += int(e.decision_ok)
    for bucket in by_code.values():
        bucket["accuracy"] = bucket["correct"] / bucket["total"]

    by_class: dict[str, dict[str, Any]] = {}
    for e in evals:
        bucket = by_class.setdefault(e.expected_decision, {"correct": 0, "total": 0})
        bucket["total"] += 1
        bucket["correct"] += int(e.decision_ok)
    for bucket in by_class.values():
        bucket["accuracy"] = bucket["correct"] / bucket["total"]

    # false approval rate: expected non-approve but agent approved.
    catastrophic = [e for e in evals if e.expected_decision in ("request_more_info", "escalate")]
    false_approvals = [e for e in catastrophic if e.agent_decision == "approve"]
    far = len(false_approvals) / len(catastrophic) if catastrophic else 0.0

    middle = [e for e in evals if e.expected_decision == MIDDLE_CLASS_DECISION]
    overrides = [e for e in evals if e.overridden]
    # Raw operational-failure tally, kept OUT of every domain figure above. Turning this
    # into rates / breakdowns / confidence intervals belongs to the reporting milestone.
    system_failures = [e for e in evals if e.system_failure_count > 0]

    return {
        "n_cases": n,
        "decision_accuracy_overall": _acc([e.decision_ok for e in evals]),
        "decision_accuracy_by_taxonomy_code": by_code,
        "decision_accuracy_by_decision_class": by_class,
        "finding_accuracy": _acc([e.finding_ok for e in evals]),
        "trajectory_correctness": _acc([e.trajectory_ok for e in evals]),
        "extraction_accuracy_mean": (
            sum(e.extraction_acc for e in evals) / n if n else 1.0
        ),
        "false_approval_rate": far,
        "false_approvals": [e.case_id for e in false_approvals],
        "middle_class_fidelity": {
            "label": "diagnostic (n={}), not statistical".format(len(middle)),
            "n": len(middle),
            "accuracy": _acc([e.decision_ok for e in middle]),
            "cases": [e.case_id for e in middle],
        },
        "guardrail_override_count": len(overrides),
        "guardrail_overrides": [e.case_id for e in overrides],
        "system_failures": {
            "label": "operational failures (T-family); excluded from every domain metric above",
            "n_runs_with_failure": len(system_failures),
            "codes": sorted({c for e in system_failures for c in e.system_error_codes}),
            "cases": [e.case_id for e in system_failures],
        },
        "per_case": [
            {
                "case_id": e.case_id,
                "injected_codes": e.injected_codes,
                "agent_codes": e.agent_codes,
                "expected_decision": e.expected_decision,
                "agent_decision": e.agent_decision,
                "decision_match": e.decision_ok,
                "finding_match": e.finding_ok,
                "trajectory_match": e.trajectory_ok,
                "extraction_accuracy": e.extraction_acc,
                "overridden": e.overridden,
                "expected_skipped": e.expected_skipped,
                "agent_skipped": e.agent_skipped,
            }
            for e in evals
        ],
    }

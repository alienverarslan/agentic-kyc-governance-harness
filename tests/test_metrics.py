"""Metrics: false_approval_rate, finding_match, and the 'right decision, wrong
trajectory' failure — all on hand-built inputs."""

from harness.contracts.findings import (
    AgentResult,
    Finding,
    GuardrailDecision,
    SynthesisProposal,
)
from harness.contracts.truth import DecisionTruth
from harness.eval.metrics import (
    CaseEvaluation,
    build_report,
    finding_match,
    trajectory_match,
)

FULL_TRAJ = [
    "extract",
    "resolve_entities",
    "check_identity_consistency",
    "check_ownership_consistency",
    "check_authority_chain",
    "check_completeness",
    "synthesize",
    "guardrail",
    "act",
]


def _result(decision, codes, trajectory, skipped=None):
    findings = [
        Finding(check_name="t", code=c, severity="explainable", detail="", fields_involved=[])
        for c in codes
        if c != "NONE"
    ]
    return AgentResult(
        case_ref="t",
        decision=decision,
        trajectory=trajectory,
        skipped_checks=skipped or [],
        guardrail=GuardrailDecision(final_decision=decision, overridden=False),
        proposal=SynthesisProposal(proposed_decision=decision, reasoning="", key_findings=[]),
        findings=findings,
    )


def _truth(decision, codes, trajectory, skipped=None):
    return DecisionTruth(
        expected_decision=decision,
        expected_trajectory=trajectory,
        expected_skipped_checks=skipped or [],
        injected_codes=codes,
    )


def test_finding_match_sets():
    r = _result("request_more_info", ["B1a"], FULL_TRAJ)
    assert finding_match(r, _truth("request_more_info", ["B1a"], FULL_TRAJ))
    assert not finding_match(r, _truth("request_more_info", ["B1b"], FULL_TRAJ))
    # clean case: empty findings normalize to {"NONE"}.
    clean = _result("approve", [], FULL_TRAJ)
    assert finding_match(clean, _truth("approve", ["NONE"], FULL_TRAJ))


def test_right_decision_wrong_trajectory_fails():
    # Correct decision but a required check node is missing from the trajectory.
    bad_traj = [n for n in FULL_TRAJ if n != "check_authority_chain"]
    r = _result("approve", ["NONE"], bad_traj)
    t = _truth("approve", ["NONE"], FULL_TRAJ)
    assert r.decision == t.expected_decision  # decision is right...
    assert not trajectory_match(r, t)  # ...but the trajectory is wrong -> FAIL


def test_skipped_check_reported_as_ran_clean_fails():
    # E1 shape: ownership+authority should be SKIPPED. Reporting them as ran (present in
    # trajectory, empty skipped set) must fail trajectory_match.
    truth = _truth(
        "request_more_info",
        ["E1"],
        ["extract", "resolve_entities", "check_identity_consistency", "check_completeness", "synthesize", "guardrail", "act"],
        skipped=["check_ownership_consistency", "check_authority_chain"],
    )
    liar = _result("request_more_info", ["E1"], FULL_TRAJ, skipped=[])
    assert not trajectory_match(liar, truth)


def test_false_approval_rate_isolated():
    # Two non-approve cases; one is wrongly approved -> FAR = 0.5, kept separate from
    # overall decision accuracy (which is 2/3 here).
    evals = [
        CaseEvaluation("c1", ["NONE"], "approve", "approve", True, True, ["NONE"], True, 1.0, False, [], []),
        CaseEvaluation("c2", ["B1b"], "escalate", "approve", False, True, ["B1b"], True, 1.0, False, [], []),
        CaseEvaluation("c3", ["C1b"], "escalate", "escalate", True, True, ["C1b"], True, 1.0, False, [], []),
    ]
    report = build_report(evals)
    assert report["false_approval_rate"] == 0.5  # 1 of 2 non-approve cases approved
    assert report["false_approvals"] == ["c2"]
    # Overall decision accuracy is a DIFFERENT, higher number: FAR is not averaged in.
    assert report["decision_accuracy_overall"] == 2 / 3
    assert report["decision_accuracy_overall"] != report["false_approval_rate"]

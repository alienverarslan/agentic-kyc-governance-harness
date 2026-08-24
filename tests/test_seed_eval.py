"""The whole-set assertion: a correctly built agent scores perfectly on the seed set,
with false_approval_rate == 0. Runs offline via the stub."""

from harness.eval.metrics import build_report
from harness.eval.run import run_eval
from harness.llm.stub import PolicyMirrorStub


def test_perfect_scores_on_seed_set():
    report = run_eval(PolicyMirrorStub())
    assert report["n_cases"] == 8
    assert report["decision_accuracy_overall"] == 1.0
    assert report["finding_accuracy"] == 1.0
    assert report["trajectory_correctness"] == 1.0
    assert report["extraction_accuracy_mean"] == 1.0
    # Headline catastrophic metric.
    assert report["false_approval_rate"] == 0.0
    assert report["false_approvals"] == []
    # A well-calibrated stub never triggers an override.
    assert report["guardrail_override_count"] == 0
    # Middle class (#4,#6,#8) is diagnostic and labeled as such.
    mc = report["middle_class_fidelity"]
    assert mc["n"] == 3
    assert mc["accuracy"] == 1.0
    assert "diagnostic" in mc["label"]


def test_minimal_difference_pairs_are_separated():
    # 4/5 (B1a vs B1b) and 6/7 (C1a vs C1b) must land on different decisions.
    report = run_eval(PolicyMirrorStub())
    per = {c["case_id"]: c for c in report["per_case"]}
    assert per["case_04"]["agent_decision"] == "request_more_info"
    assert per["case_05"]["agent_decision"] == "escalate"
    assert per["case_06"]["agent_decision"] == "request_more_info"
    assert per["case_07"]["agent_decision"] == "escalate"

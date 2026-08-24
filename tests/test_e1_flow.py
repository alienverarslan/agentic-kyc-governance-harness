"""E1 flow (seed #8): a missing document must SKIP the checks that need it, and those
skips must be recorded as skips — never as 'ran clean'."""

from harness.agent.runner import run_agent
from harness.data.loader import load_case
from harness.eval.metrics import trajectory_match
from harness.llm.stub import PolicyMirrorStub


def test_missing_ubo_skips_ownership_and_authority():
    case = load_case("case_08")  # ubo = null
    result = run_agent(case.dossier, PolicyMirrorStub(), case_ref="case_08")

    assert result.decision == "request_more_info"
    # ubo=null skips the three checks that need B3 (ownership, authority, ubo_derivation).
    assert set(result.skipped_checks) == {
        "check_ownership_consistency",
        "check_authority_chain",
        "check_ubo_derivation",
    }
    # Skipped checks are NOT in the executed trajectory.
    assert "check_ownership_consistency" not in result.trajectory
    assert "check_authority_chain" not in result.trajectory
    assert "check_ubo_derivation" not in result.trajectory
    # They are recorded as ran=False (not ran-clean).
    by_name = {c.check_name: c for c in result.check_results}
    assert by_name["check_ownership_consistency"].ran is False
    assert by_name["check_authority_chain"].ran is False
    assert by_name["check_ubo_derivation"].ran is False
    # E1 finding present.
    assert any(f.code == "E1" for f in result.findings)
    # And the trajectory matches ground truth.
    assert trajectory_match(result, case.decision_truth)

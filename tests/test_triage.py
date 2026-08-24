"""Faz 2: the AI triage layer's fail-closed wiring.

The safety property under test: a triage concern can only ever RAISE caution — it can
push a decision toward request_more_info / escalate, but NEVER cause an approval, even
when the synthesis proposer is maximally lax. The AI's detection *quality* (does it
actually notice a residential-address/heavy-industry mismatch?) needs a real model and is
observed via harness.agent.triage_demo, not asserted here.
"""

from harness.agent.runner import run_agent
from harness.contracts.findings import SynthesisProposal
from harness.data.loader import load_dossier
from harness.llm.stub import PolicyMirrorStub, ScriptedTriageStub


def _codes(result):
    return {f.code for f in result.findings}


def test_noop_triage_leaves_clean_case_untouched():
    r = run_agent(load_dossier("case_01"), PolicyMirrorStub(), case_ref="case_01")
    assert r.decision == "approve"
    assert not any(c.startswith("X") for c in _codes(r))
    assert "ai_triage" in r.trajectory
    assert r.triage is not None and r.triage.novel_concerns == []


def test_explainable_concern_raises_clean_case_to_request_more_info():
    stub = ScriptedTriageStub([("address looks residential for heavy industry", "explainable")])
    r = run_agent(load_dossier("case_01"), stub, case_ref="case_01")
    assert r.decision == "request_more_info"
    assert "X1" in _codes(r)
    # The proposer saw no deterministic findings and proposed approve; the guardrail
    # raised it because of the AI concern -> a logged override.
    assert r.guardrail.overridden is True


def test_unexplainable_concern_escalates():
    stub = ScriptedTriageStub([("declared owner appears on a sanctions list", "unexplainable")])
    r = run_agent(load_dossier("case_01"), stub, case_ref="case_01")
    assert r.decision == "escalate"
    assert "X2" in _codes(r)


def test_severity_is_clamped_so_ai_can_never_lower():
    # An "info" (or any non-'unexplainable') severity is clamped up to explainable, so the
    # AI cannot emit a non-raising finding.
    stub = ScriptedTriageStub([("minor note", "info")])
    r = run_agent(load_dossier("case_01"), stub, case_ref="case_01")
    assert r.decision == "request_more_info"
    assert "X1" in _codes(r)


class _LaxWithConcern(ScriptedTriageStub):
    """Raises a concern in triage AND always proposes approve in synthesis."""

    def _synth(self, user: str) -> SynthesisProposal:
        return SynthesisProposal(proposed_decision="approve", reasoning="lax", key_findings=[])


def test_lax_proposer_plus_concern_still_cannot_approve():
    stub = _LaxWithConcern([("unusual ownership path narrative", "explainable")])
    r = run_agent(load_dossier("case_01"), stub, case_ref="case_01")
    assert r.decision != "approve"
    assert r.guardrail.overridden is True


def test_fpr_measurement_is_zero_with_noop_triage():
    # The measurement harness itself: with a no-op triage stub, clean cases must show a
    # 0% false-positive rate (this validates the measurement, offline).
    from harness.agent.triage_fpr import measure

    report = measure(PolicyMirrorStub(), n=6, seed=3)
    assert report["clean_confirmed"] == 6
    assert report["false_positive_rate"] == 0.0
    assert report["over_escalation_rate"] == 0.0


def test_fpr_measurement_counts_scripted_false_positives():
    # A scripted stub that always raises one explainable concern -> 100% false positives,
    # all landing on request_more_info (never escalate). Confirms the aggregation.
    from harness.agent.triage_fpr import measure

    stub = ScriptedTriageStub([("spurious concern", "explainable")])
    report = measure(stub, n=5, seed=9)
    assert report["false_positive_rate"] == 1.0
    assert report["request_more_info_count"] == 5
    assert report["escalate_count"] == 0


def test_triage_does_not_regress_deterministic_decisions():
    # With a no-op triage (PolicyMirrorStub), a sample of escalate/rmi/approve fixtures
    # keep their deterministic outcome and produce no X findings.
    expect = {"case_01": "approve", "case_05": "escalate", "case_08": "request_more_info"}
    for case_id, decision in expect.items():
        r = run_agent(load_dossier(case_id), PolicyMirrorStub(), case_ref=case_id)
        assert r.decision == decision
        assert not any(c.startswith("X") for c in _codes(r))

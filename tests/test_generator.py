"""Slice-2 synthetic generator tests.

The central test is METAMORPHIC self-validation: every generated case, when screened,
must produce agent finding codes EQUAL to the single injected code — and the correct
decision and trajectory. If an injector accidentally triggers a second code, this fails
loudly. This is how the generator is 'validated against the fixtures': it is held to the
same deterministic harness the 8 read-only seeds are.
"""

import pytest

from harness.agent.runner import run_agent
from harness.eval.metrics import (
    agent_finding_codes,
    decision_match,
    finding_match,
    trajectory_match,
)
from harness.generate.synthetic import ALL_CODES, ESCALATE_CODES, generate_cases
from harness.generate.run import run_generation_eval
from harness.llm.stub import AdversarialStub, OvercautiousStub, PolicyMirrorStub

# Modest per-code volume keeps the suite fast; the CLI default is 30.
PER_CODE = 8
SEED = 20260706


@pytest.fixture(scope="module")
def cases():
    return generate_cases(per_code=PER_CODE, seed=SEED)


def test_reproducible_given_seed():
    a = generate_cases(per_code=4, seed=1)
    b = generate_cases(per_code=4, seed=1)
    assert [g.case_id for g in a] == [g.case_id for g in b]
    assert [g.case.dossier.model_dump(mode="json") for g in a] == [
        g.case.dossier.model_dump(mode="json") for g in b
    ]


def test_covers_every_taxonomy_code(cases):
    produced = {g.code for g in cases}
    assert produced == set(ALL_CODES)


def test_every_generated_case_isolates_its_code(cases):
    """METAMORPHIC: agent codes == injected code, decision + trajectory correct."""
    stub = PolicyMirrorStub()
    failures = []
    for gc in cases:
        r = run_agent(gc.case.dossier, stub, case_ref=gc.case_id)
        dt = gc.case.decision_truth
        if not (finding_match(r, dt) and decision_match(r, dt) and trajectory_match(r, dt)):
            failures.append((gc.case_id, gc.code, sorted(agent_finding_codes(r)), r.decision))
    assert not failures, f"{len(failures)} cases failed isolation: {failures[:5]}"


def test_boundary_variants_exist_and_pass(cases):
    boundary = [g for g in cases if g.tier == "boundary"]
    assert boundary, "expected some boundary-tier cases"
    stub = PolicyMirrorStub()
    for gc in boundary:
        r = run_agent(gc.case.dossier, stub, case_ref=gc.case_id)
        assert finding_match(r, gc.case.decision_truth), (gc.case_id, gc.code)


def test_adversarial_lax_llm_cannot_cause_false_approval(cases):
    """Even when the LLM always proposes approve, the guardrail keeps FAR = 0 and the
    final decision correct on every escalate-worthy case."""
    lax = AdversarialStub()
    overrides = 0
    for gc in cases:
        r = run_agent(gc.case.dossier, lax, case_ref=gc.case_id)
        # No escalate case is ever finally approved.
        if gc.code in ESCALATE_CODES:
            assert r.decision == "escalate", (gc.case_id, gc.code, r.decision)
            assert r.guardrail.overridden is True
        # And no non-approve case is approved.
        if gc.case.decision_truth.expected_decision != "approve":
            assert r.decision != "approve"
        overrides += int(r.guardrail.overridden)
    assert overrides > 0  # the guardrail actually did work


def test_overcautious_llm_is_pulled_back_down(cases):
    """When the LLM always proposes escalate, the guardrail overrides the non-escalate
    cases down to the correct decision."""
    cautious = OvercautiousStub()
    for gc in cases:
        r = run_agent(gc.case.dossier, cautious, case_ref=gc.case_id)
        assert r.decision == gc.case.decision_truth.expected_decision, (gc.case_id, gc.code)
        if gc.case.decision_truth.expected_decision != "escalate":
            assert r.guardrail.overridden is True


def test_statistical_report_is_clean_at_scale():
    _cases, report = run_generation_eval(per_code=PER_CODE, seed=SEED)
    assert report["mirror"]["decision_accuracy_overall"] == 1.0
    assert report["mirror"]["finding_accuracy"] == 1.0
    assert report["mirror"]["trajectory_correctness"] == 1.0
    # The headline safety property under an adversarial proposer.
    assert report["adversarial_lax"]["false_approval_rate"] == 0.0
    assert report["adversarial_lax"]["decision_accuracy_overall"] == 1.0
    assert report["adversarial_lax"]["guardrail_override_count"] > 0
    assert report["overcautious"]["decision_accuracy_overall"] == 1.0

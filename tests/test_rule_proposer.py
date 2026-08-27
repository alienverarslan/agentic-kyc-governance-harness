"""Faz 3 (part 2): the LLM-assisted rule proposer, offline via ScriptedProposerStub.

The property under test: the LLM may ONLY select an existing template_id and fill its
numeric params — an unknown template_id, out-of-bounds params, or "nothing fits" are all
handled without ever producing an effective rule on their own. A structurally-accepted
proposal is still just a CandidateRule; it must separately pass the validation gate.
"""

from harness.llm.stub import ScriptedProposerStub
from harness.rules.gate import run_validation_gate
from harness.rules.proposer import build_proposer_user, evidence_from_anomaly_type, propose_rule


def test_well_formed_proposal_is_structurally_accepted():
    stub = ScriptedProposerStub(
        template_id="capital_age_ceiling",
        params={"max_age_days": 180, "max_capital": 10_000_000},
        rationale="weeks-old companies declaring tens of millions in capital",
    )
    outcome = propose_rule(stub, "capital-age-v1", ["weeks-old SME with 75M+ capital"])
    assert outcome.accepted is True
    assert outcome.rule is not None
    assert outcome.rule.rule_id == "capital-age-v1"
    assert outcome.rule.template_id == "capital_age_ceiling"
    assert outcome.rule.params == {"max_age_days": 180, "max_capital": 10_000_000}
    assert outcome.rule.proposed_by == "llm"


def test_declining_to_propose_is_handled_without_error():
    stub = ScriptedProposerStub(template_id="", params={})
    outcome = propose_rule(stub, "some-rule", ["evidence that fits nothing"])
    assert outcome.accepted is False
    assert outcome.rule is None
    assert "declined" in outcome.reason


def test_unknown_template_id_is_rejected_not_invented():
    stub = ScriptedProposerStub(template_id="a_template_that_does_not_exist", params={"x": 1})
    outcome = propose_rule(stub, "bad-rule", ["some evidence"])
    assert outcome.accepted is False
    assert outcome.rule is None
    assert "unknown template" in outcome.reason


def test_out_of_bounds_params_are_rejected():
    stub = ScriptedProposerStub(
        template_id="capital_age_ceiling",
        params={"max_age_days": 999999, "max_capital": 10_000_000},
    )
    outcome = propose_rule(stub, "bad-params", ["some evidence"])
    assert outcome.accepted is False
    assert outcome.rule is None
    assert "invalid" in outcome.reason


def test_missing_params_are_rejected():
    stub = ScriptedProposerStub(template_id="capital_age_ceiling", params={"max_age_days": 180})
    outcome = propose_rule(stub, "missing-params", ["some evidence"])
    assert outcome.accepted is False
    assert outcome.rule is None


def test_accepted_proposal_still_must_pass_the_gate_independently():
    # A structurally-accepted proposal is not automatically safe: it goes through the
    # exact same non-regression gate as a directly constructed candidate rule.
    stub = ScriptedProposerStub(
        template_id="capital_age_ceiling",
        params={"max_age_days": 180, "max_capital": 10_000_000},
    )
    outcome = propose_rule(stub, "capital-age-v1", ["weeks-old SME with 75M+ capital"])
    assert outcome.accepted is True

    gate_result = run_validation_gate(outcome.rule, per_code=10, seed=1)
    assert gate_result.passed is True
    assert gate_result.regressions == []


def test_accepted_proposal_can_still_fail_the_gate_if_badly_parameterized():
    # Structural acceptance (valid template + in-bounds params) does not imply the gate
    # will pass — a technically-valid but reckless proposal is still caught downstream.
    stub = ScriptedProposerStub(
        template_id="capital_age_ceiling",
        params={"max_age_days": 1825, "max_capital": 100_000},
    )
    outcome = propose_rule(stub, "reckless-rule", ["some evidence"])
    assert outcome.accepted is True

    gate_result = run_validation_gate(outcome.rule, per_code=10, seed=1)
    assert gate_result.passed is False
    assert gate_result.regressions


def test_build_proposer_user_lists_templates_and_evidence():
    payload = build_proposer_user(["a missed anomaly detail"])
    assert "capital_age_ceiling" in payload
    assert "max_age_days" in payload
    assert "a missed anomaly detail" in payload


def test_evidence_from_anomaly_type_returns_matching_details():
    evidence = evidence_from_anomaly_type("implausible_capital", per_type=3, seed=42)
    assert len(evidence) == 3
    assert all(isinstance(e, str) and e for e in evidence)

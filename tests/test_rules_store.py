"""Faz 3 (part 4): the promoted-rule store enforces human approval + a passed gate in
code, not just by convention. Every test uses a tmp_path override so it never touches the
repo's real artifacts/promoted_rules.json.
"""

import pytest

from harness.rules.gate import GateResult, run_validation_gate
from harness.rules.schema import CandidateRule
from harness.rules.store import load_promoted_rules, promote_rule, save_promoted_rules

_RULE = CandidateRule(
    rule_id="capital-age-v1",
    template_id="capital_age_ceiling",
    params={"max_age_days": 180, "max_capital": 10_000_000},
)


def test_store_starts_empty(tmp_path):
    assert load_promoted_rules(tmp_path / "promoted.json") == []


def test_promote_requires_nonempty_approved_by(tmp_path):
    path = tmp_path / "promoted.json"
    passed = GateResult(rule_id=_RULE.rule_id, template_id=_RULE.template_id, passed=True)
    with pytest.raises(ValueError):
        promote_rule(_RULE, approved_by="", gate_report=passed, path=path)
    with pytest.raises(ValueError):
        promote_rule(_RULE, approved_by="   ", gate_report=passed, path=path)
    assert load_promoted_rules(path) == []


def test_promote_requires_gate_to_have_passed(tmp_path):
    path = tmp_path / "promoted.json"
    failed = GateResult(
        rule_id=_RULE.rule_id, template_id=_RULE.template_id, passed=False, regressions=[{"x": "y"}]
    )
    with pytest.raises(ValueError):
        promote_rule(_RULE, approved_by="Arslan", gate_report=failed, path=path)
    assert load_promoted_rules(path) == []


def test_promote_requires_gate_report_for_the_same_rule(tmp_path):
    path = tmp_path / "promoted.json"
    mismatched = GateResult(rule_id="some-other-rule", template_id=_RULE.template_id, passed=True)
    with pytest.raises(ValueError):
        promote_rule(_RULE, approved_by="Arslan", gate_report=mismatched, path=path)
    assert load_promoted_rules(path) == []


def test_promote_succeeds_with_approval_and_passed_gate_then_persists(tmp_path):
    path = tmp_path / "promoted.json"
    result = run_validation_gate(_RULE, per_code=10, seed=42)
    assert result.passed

    promote_rule(_RULE, approved_by="Arslan", gate_report=result, path=path)

    reloaded = load_promoted_rules(path)
    assert len(reloaded) == 1
    assert reloaded[0].rule_id == _RULE.rule_id
    assert reloaded[0].params == _RULE.params


def test_promote_refuses_duplicate_rule_id(tmp_path):
    path = tmp_path / "promoted.json"
    result = run_validation_gate(_RULE, per_code=5, seed=1)
    promote_rule(_RULE, approved_by="Arslan", gate_report=result, path=path)
    with pytest.raises(ValueError):
        promote_rule(_RULE, approved_by="Arslan", gate_report=result, path=path)


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "promoted.json"
    save_promoted_rules([_RULE], path)
    reloaded = load_promoted_rules(path)
    assert reloaded == [_RULE]

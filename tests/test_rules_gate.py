"""Faz 3 (part 3): the offline validation gate.

The load-bearing property under test: a candidate rule that fires on ANY of the 8 seed
fixtures or the 420 generated cases is REJECTED, regardless of how useful it looks on the
anomaly corpus. A sensibly-parameterized capital_age_ceiling rule passes (none of the known
cases involve a young company with implausible capital); a badly-parameterized one (too low
a capital ceiling) legitimately misfires on the generator's clean bases and is rejected.
"""

from harness.rules.gate import render, run_validation_gate
from harness.rules.schema import CandidateRule


def test_well_parameterized_rule_passes_with_zero_regressions():
    rule = CandidateRule(
        rule_id="capital-age-v1",
        template_id="capital_age_ceiling",
        params={"max_age_days": 180, "max_capital": 10_000_000},
    )
    result = run_validation_gate(rule, per_code=30, seed=42)
    assert result.passed is True
    assert result.regressions == []
    assert result.n_checked == 8 + 30 * 14  # 8 seed fixtures + 14 taxonomy codes x 30

    # Informational efficacy preview: it should actually catch the anomaly it targets.
    assert result.anomaly_preview is not None
    assert result.anomaly_preview["anomaly_type"] == "implausible_capital"
    assert result.anomaly_preview["caught"] == result.anomaly_preview["total"] > 0

    # Renders without raising, for the CLI's human-readable report.
    assert "PASSED" in render(result)


def test_badly_parameterized_rule_is_rejected_for_regressions():
    # max_capital=100,000 is below every clean-base share_capital (500k-2.5M) and
    # max_age_days=1825 (5 years) overlaps the generator's incorporation-age range
    # (3-12 years before reg_date), so this legitimately misfires on clean cases.
    rule = CandidateRule(
        rule_id="capital-age-too-strict",
        template_id="capital_age_ceiling",
        params={"max_age_days": 1825, "max_capital": 100_000},
    )
    result = run_validation_gate(rule, per_code=10, seed=42)
    assert result.passed is False
    assert len(result.regressions) > 0
    assert "REJECTED" in render(result)


def test_invalid_params_are_rejected_before_touching_the_corpus():
    rule = CandidateRule(
        rule_id="bad-bounds",
        template_id="capital_age_ceiling",
        params={"max_age_days": 999999, "max_capital": 10_000_000},
    )
    result = run_validation_gate(rule, per_code=5, seed=1)
    assert result.passed is False
    assert result.n_checked == 0
    assert any("out of allowed bounds" in e for e in result.param_errors)


def test_unknown_template_is_rejected():
    rule = CandidateRule(rule_id="nope", template_id="does_not_exist", params={})
    result = run_validation_gate(rule)
    assert result.passed is False
    assert any("unknown template" in e for e in result.param_errors)

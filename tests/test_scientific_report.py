"""P5 aggregator — the five surfaces, the F1 paired-diff integrity guard, and the
counterfactual operational attribution, plus a golden offline end-to-end build.
"""

import pytest

from harness.eval import scientific_report as sr
from harness.eval.scientific_report import RunRecord


def _rec(case_id, expected, agent, *, injected=None, sev_origin=None, sysfail=0, syscodes=None):
    injected = injected if injected is not None else []
    return RunRecord(
        case_id=case_id,
        expected_decision=expected,
        agent_decision=agent,
        decision_ok=(agent == expected),
        finding_ok=True,
        injected_codes=injected,
        agent_codes=list(injected) or ["NONE"],
        finding_severities_by_origin=sev_origin or [],
        system_failure_count=sysfail,
        system_error_codes=syscodes or [],
    )


# --- Surface 1: deterministic engine --------------------------------------------------
def test_deterministic_engine_accuracy_and_ci():
    recs = [_rec(f"c{i}", "approve", "approve") for i in range(10)]
    m = sr.build_deterministic_engine_metrics(recs)
    assert m["decision_accuracy"]["k"] == 10
    assert m["decision_accuracy"]["n"] == 10
    assert m["decision_accuracy"]["point_estimate"] == 1.0
    assert m["decision_accuracy"]["wilson_ci_95"] is not None


# --- Surface 1b: F1 paired diff -------------------------------------------------------
def test_learned_rule_marginal_paired_diff():
    # 3 cases wrong without the rule become right with it; none regress.
    without = [_rec("a", "escalate", "approve"), _rec("b", "escalate", "approve"),
               _rec("c", "escalate", "approve"), _rec("d", "approve", "approve")]
    with_rule = [_rec("a", "escalate", "escalate"), _rec("b", "escalate", "escalate"),
                 _rec("c", "escalate", "escalate"), _rec("d", "approve", "approve")]
    lr = sr.build_learned_rule_marginal(with_rule, without, ["capital-age-v1"])
    assert lr["evaluated_cases"] == 4
    assert lr["correct_without_rule"] == 1
    assert lr["correct_with_rule"] == 4
    assert lr["newly_correct_due_to_rule"] == 3
    assert lr["newly_incorrect_due_to_rule"] == 0
    assert lr["net_correctness_delta"] == 3
    assert lr["marginal_gain_rate"]["k"] == 3
    assert lr["marginal_gain_rate"]["n"] == 4


def test_learned_rule_marginal_reports_regressions():
    without = [_rec("a", "approve", "approve")]
    with_rule = [_rec("a", "approve", "escalate")]  # rule broke a previously-correct case
    lr = sr.build_learned_rule_marginal(with_rule, without, ["bad-rule"])
    assert lr["newly_incorrect_due_to_rule"] == 1
    assert lr["net_correctness_delta"] == -1


def test_learned_rule_marginal_fails_closed_on_mismatched_case_sets():
    without = [_rec("a", "approve", "approve"), _rec("b", "approve", "approve")]
    with_rule = [_rec("a", "approve", "approve"), _rec("c", "approve", "approve")]
    with pytest.raises(ValueError, match="identical case-id sets"):
        sr.build_learned_rule_marginal(with_rule, without, [])


# --- Surface 5: operational reliability + counterfactual attribution -------------------
def test_system_failure_counted_but_not_induced_when_domain_already_escalates():
    # A system finding co-occurs with an independent A2 (unexplainable domain finding).
    # The counterfactual (strip system) still escalates -> failure counted, NOT induced.
    rec = _rec("x", "escalate", "escalate", sysfail=1, syscodes=["T0"],
               sev_origin=[("unexplainable", "system"), ("unexplainable", "deterministic")])
    op = sr.build_operational_reliability([rec])
    assert op["system_failure_rate"]["k"] == 1
    assert op["system_induced_escalation_rate_all_runs"]["k"] == 0  # not attributable to T
    assert op["system_induced_escalation_rate_given_system_failure"]["k"] == 0
    assert op["system_induced_escalation_rate_given_system_failure"]["n"] == 1


def test_system_induced_escalation_when_t_finding_is_the_only_cause():
    # Only a system finding elevates the decision; counterfactual would be approve.
    rec = _rec("y", "approve", "escalate", sysfail=1, syscodes=["T0"],
               sev_origin=[("unexplainable", "system")])
    op = sr.build_operational_reliability([rec])
    assert op["system_failure_rate"]["k"] == 1
    assert op["system_induced_escalation_rate_all_runs"]["k"] == 1
    assert op["system_induced_escalation_rate_given_system_failure"]["k"] == 1


def test_operational_rates_are_zero_and_ratestat_shaped_with_no_failures():
    recs = [_rec("a", "approve", "approve"), _rec("b", "approve", "approve")]
    op = sr.build_operational_reliability(recs)
    assert op["system_failure_rate"]["k"] == 0
    # given-system-failure has n=0 -> explicit N/A, not a fabricated 0.
    assert op["system_induced_escalation_rate_given_system_failure"]["n"] == 0
    assert op["system_induced_escalation_rate_given_system_failure"]["point_estimate"] is None


# --- Surface 4: final guardrail safety ------------------------------------------------
def test_false_approval_rate_and_scope():
    recs = [_rec("a", "escalate", "escalate"), _rec("b", "request_more_info", "request_more_info"),
            _rec("c", "approve", "approve")]
    fg = sr.build_final_guardrail_safety(recs, overridden_count=0)
    assert fg["false_approval_rate"]["k"] == 0
    assert fg["false_approval_rate"]["n"] == 2  # only non-approve-truth cases in denominator
    assert "not an end-to-end guarantee" in fg["false_approval_rate_scope"]
    assert fg["system_present_implies_no_approve"]["type"] == "structural_invariant"


def test_false_approval_rate_counts_a_real_false_approval():
    recs = [_rec("a", "escalate", "approve")]  # should have escalated, approved instead
    fg = sr.build_final_guardrail_safety(recs, overridden_count=0)
    assert fg["false_approval_rate"]["k"] == 1
    assert fg["false_approvals"] == ["a"]


# --- Golden offline end-to-end --------------------------------------------------------
def test_offline_report_is_reproducible_and_clean():
    r1 = sr.build_offline_report(per_code=2, seed=20260717)
    r2 = sr.build_offline_report(per_code=2, seed=20260717)

    assert r1["schema_version"] == sr.SCHEMA_VERSION
    # Deterministic corpus hash is stable across identical builds.
    assert r1["corpus"]["corpus_hash"] == r2["corpus"]["corpus_hash"]

    det = r1["metrics"]["deterministic_engine"]
    # Mirror stub is well-calibrated: perfect decision accuracy at scale.
    assert det["decision_accuracy"]["point_estimate"] == 1.0
    assert det["finding_accuracy"]["point_estimate"] == 1.0

    # Headline safety: false_approval_rate is 0 within this evaluated corpus.
    fg = r1["metrics"]["final_guardrail_safety"]
    assert fg["false_approval_rate"]["k"] == 0

    # A promoted rule must never regress a previously-correct case.
    lr = det["learned_rule_marginal"]
    assert lr["newly_incorrect_due_to_rule"] == 0

    # Offline artifact declares the live surfaces unmeasured rather than fabricating them.
    assert "live-only" in r1["metrics"]["ai_triage_recall"]["note"]
    assert "live-only" in r1["metrics"]["ai_triage_fpr"]["note"]


def test_corpus_hash_is_sensitive_to_corpus_shape():
    r_small = sr.build_offline_report(per_code=2, seed=20260717)
    r_big = sr.build_offline_report(per_code=3, seed=20260717)
    assert r_small["corpus"]["corpus_hash"] != r_big["corpus"]["corpus_hash"]


def test_markdown_renders_without_error_and_shows_scope():
    report = sr.build_offline_report(per_code=2, seed=20260717)
    md = sr.render_markdown(report)
    assert "# P5 Scientific Report" in md
    assert "not an end-to-end guarantee" in md
    assert "structural_invariant" in md

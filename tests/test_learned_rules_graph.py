"""Faz 3 (part 1, graph wiring): check_learned_rules is a real node in the graph.

Three things this proves, all offline (PolicyMirrorStub, no API key):
1. With zero promoted rules (today's actual default), wiring the node in changes NOTHING
   — the 8 seed fixtures decide exactly as before.
2. A malformed promoted rule becomes a T1 (origin="system", unexplainable -> escalate)
   finding rather than silently vanishing or crashing the whole run (fail-closed defense in
   depth). See tests/test_system_errors.py for the full operational-failure contract.
3. The concrete motivating result: promoting a capital_age_ceiling rule turns a
   deterministically-clean "implausible capital" anomaly-corpus dossier into
   request_more_info — closing the Faz 4 recall gap without touching the LLM at all.
"""

from harness.agent.anomaly_corpus import build_anomaly_corpus
from harness.agent.learned_rules import check_learned_rules
from harness.agent.runner import run_agent
from harness.data.loader import load_dossier
from harness.llm.stub import PolicyMirrorStub
from harness.rules.schema import CandidateRule

_GOOD_RULE = CandidateRule(
    rule_id="capital-age-v1",
    template_id="capital_age_ceiling",
    params={"max_age_days": 180, "max_capital": 10_000_000},
)


def test_check_learned_rules_is_noop_with_zero_rules():
    dossier = load_dossier("case_01")
    result = check_learned_rules(dossier, [])
    assert result.ran is True
    assert result.findings == []


def test_check_learned_rules_wraps_a_broken_rule_as_t1_instead_of_crashing():
    broken = CandidateRule(rule_id="broken", template_id="capital_age_ceiling", params={"max_age_days": 180})
    dossier = load_dossier("case_01")
    result = check_learned_rules(dossier, [broken])
    assert result.ran is True
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.code == "T1"
    assert finding.severity == "unexplainable"
    assert finding.origin == "system"
    assert finding.error_kind == "rule_runtime_error"


def test_graph_with_zero_promoted_rules_does_not_regress_seed_decisions():
    expect = {"case_01": "approve", "case_05": "escalate", "case_08": "request_more_info"}
    for case_id, decision in expect.items():
        r = run_agent(load_dossier(case_id), PolicyMirrorStub(), case_ref=case_id, promoted_rules=[])
        assert r.decision == decision
        assert "check_learned_rules" in r.trajectory
        assert not any(f.code.startswith(("F", "T")) for f in r.findings)


def test_promoting_capital_age_ceiling_catches_the_faz4_recall_gap():
    # Every implausible_capital case is deterministically clean today (Faz 4 baseline).
    anomalies = [c for c in build_anomaly_corpus(seed=42, per_type=3) if c.anomaly_type == "implausible_capital"]
    assert anomalies

    for case in anomalies:
        before = run_agent(case.dossier, PolicyMirrorStub(), case_ref=case.dossier.dossier_id, promoted_rules=[])
        assert before.decision == "approve"
        assert not before.findings

        after = run_agent(
            case.dossier, PolicyMirrorStub(), case_ref=case.dossier.dossier_id, promoted_rules=[_GOOD_RULE]
        )
        assert after.decision == "request_more_info"
        assert any(f.code == "F1" for f in after.findings)

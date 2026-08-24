"""Faz 4 (recall side): the anomaly corpus + recall measurement harness, offline.

The load-bearing test is that every planted anomaly is DETERMINISTICALLY CLEAN — i.e. the
out-of-coverage red flag does NOT accidentally trip a real check. If it did, the
deterministic layer (not triage) would catch it and the recall measurement would be
meaningless.

Every test here passes ``promoted_rules=[]`` explicitly: they validate the corpus's
ORIGINAL baseline design (each anomaly is out-of-coverage for the fixed five checks),
independent of whatever a Faz 3 rule-learning session may have promoted on this machine
since. A promoted ``capital_age_ceiling`` rule turning the implausible-capital cases NOT
clean is the intended, load-bearing outcome of Faz 3 — not a regression here — see
``tests/test_learned_rules_graph.py`` for that behavior under test, and
``harness.agent.triage_recall.measure_recall``'s own default (real promoted state) for the
live re-measurement this corpus was built to support. The recall aggregation itself is
validated with scripted stubs.
"""

from harness.agent.anomaly_corpus import build_anomaly_corpus
from harness.agent.runner import run_agent
from harness.agent.triage_recall import measure_recall
from harness.generate.synthetic import generate_cases
from harness.llm.stub import PolicyMirrorStub, ScriptedTriageStub


def test_every_anomaly_is_deterministically_clean():
    # With a no-op triage stub, each anomaly dossier must decide 'approve' with no findings:
    # the planted flag is genuinely out-of-coverage, so only the AI layer could catch it.
    stub = PolicyMirrorStub()
    for case in build_anomaly_corpus(seed=1, per_type=3):
        r = run_agent(case.dossier, stub, case_ref=case.dossier.dossier_id, promoted_rules=[])
        assert r.decision == "approve", (case.dossier.dossier_id, case.anomaly_type, r.decision)
        assert not r.findings, (case.dossier.dossier_id, [f.code for f in r.findings])


def test_corpus_covers_all_anomaly_types():
    types = {c.anomaly_type for c in build_anomaly_corpus(seed=1, per_type=2)}
    assert types == {
        "address_activity_mismatch",
        "implausible_capital",
        "nominee_ownership_path",
        "liquidation_but_active_filing",
        "incorporation_after_registry",
        "notary_jurisdiction_mismatch",
    }


def test_recall_is_zero_when_triage_stays_silent():
    # A no-op triage catches nothing -> recall 0 / miss_rate 1 (validates the measurement).
    # promoted_rules=[] pins this to the corpus's original baseline (see module docstring).
    report = measure_recall(PolicyMirrorStub(), per_type=2, seed=5, promoted_rules=[])
    assert report["deterministically_clean"] == report["corpus_size"]
    assert report["recall"] == 0.0
    assert report["miss_rate"] == 1.0


def test_recall_is_one_when_triage_always_flags():
    # A stub that always raises a concern catches every anomaly -> recall 1.
    stub = ScriptedTriageStub([("caught it", "explainable")])
    report = measure_recall(stub, per_type=2, seed=5, promoted_rules=[])
    assert report["recall"] == 1.0
    assert report["miss_rate"] == 0.0
    # all six types at 100%
    assert all(b["recall"] == 1.0 for b in report["recall_by_type"].values())


def test_every_corpus_case_is_labeled_positive():
    # P5 contract: the anomaly corpus is entirely POSITIVE by construction — every case
    # plants a concern triage is expected to catch. Recall is only honestly named because
    # this label exists as data, not as an inferred convention.
    corpus = build_anomaly_corpus(seed=1, per_type=3)
    assert all(c.expected_triage_concern for c in corpus)


def test_recall_eligibility_breakdown_is_exposed():
    # The three eligibility counts must be reported so a shift in deterministic coverage
    # (which changes the denominator) can never be misread as triage improving.
    report = measure_recall(PolicyMirrorStub(), per_type=2, seed=5, promoted_rules=[])
    assert report["anomaly_cases_labeled_positive"] == report["corpus_size"]
    assert report["anomaly_cases_excluded_not_deterministically_clean"] == 0
    assert report["triage_recall_eligible_cases"] == report["deterministically_clean"]


def test_fpr_corpus_cases_are_none_and_expected_approve():
    # The negative/FPR corpus contract: generator NONE cases carry expected_decision
    # 'approve' and no injected codes, so a triage concern on them is a true false positive.
    cases = generate_cases(per_code=5, seed=7, codes=["NONE"])
    assert cases, "expected a non-empty NONE corpus"
    for gc in cases:
        assert gc.code == "NONE"
        assert gc.case.decision_truth.expected_decision == "approve"
        # The generator labels a clean case ['NONE'] (the seed fixtures use []); either way
        # the contract is "no real domain code injected". Assert the semantic, not the
        # surface representation.
        assert set(gc.case.decision_truth.injected_codes) <= {"NONE"}

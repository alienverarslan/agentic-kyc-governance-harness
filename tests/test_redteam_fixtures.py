"""P4 — the 30 authored red-team fixtures: inventory, per-case blindness, disjointness.

These are the fixture-DEPENDENT tests (unlike test_redteam_corpus.py, which is fixture-free).
They assert the frozen inventory (fixed rt_NN->category map, the observed 23/7/0 action
distribution, the dependency inventory), that every case is admissible under the pinned
offline blindness gate (final approve, zero findings of any origin), that the corpus is
disjoint from every tuning/decision corpus, and that the corpus hashes cleanly.

They do NOT require manifest.json; its absence before the separately-approved freeze step is
expected.
"""

from __future__ import annotations

import pytest

from harness.agent.runner import run_agent
from harness.data import redteam_corpus
from harness.data.redteam_corpus import (
    list_redteam_ids,
    load_redteam_case,
    load_redteam_dossier,
)
from harness.eval.provenance import hash_corpus, sha256_of
from harness.llm.stub import PolicyMirrorStub
from harness.rules.schema import TEMPLATE_REGISTRY
from harness.rules.store import load_promoted_rules

ALL_IDS = [f"rt_{i:02d}" for i in range(1, 31)]

# The ONLY cases whose declared concern IS a document-predates-incorporation impossibility.
# Every other fixture must keep incorporation on or before every document date, so its R-family
# concern is the single anomaly (no accidental R2-lifecycle contamination — the rt_22/24/25 bug).
LIFECYCLE_ANOMALY_IDS = {"rt_08", "rt_09", "rt_10"}

# R5 boundary intent: exact age (days) and share_capital, each placed OUTSIDE capital-age-v1's
# box (age <= 90 AND capital > 75_000_000) for a documented reason, so F1 must not fire.
R5_BOUNDARY = {
    "rt_21": {"age_days": 731, "share_capital": 5_000_000_000.0, "outside": "age > 90"},
    "rt_22": {"age_days": 31, "share_capital": 70_000_000.0, "outside": "capital <= 75M"},
    "rt_23": {"age_days_min": 91, "share_capital": 10_000.0, "outside": "no capital floor exists"},
    "rt_24": {"age_days": 30, "share_capital": 74_999_999.0, "outside": "capital <= 75M"},
    "rt_25": {"age_days": 91, "share_capital": 500_000_000.0, "outside": "age(91) > 90"},
}


# --------------------------------------------------------------------------------------
# Presence, contiguity, schema/integrity
# --------------------------------------------------------------------------------------
def test_exactly_the_thirty_contiguous_ids_exist():
    assert list_redteam_ids() == ALL_IDS


@pytest.mark.parametrize("cid", ALL_IDS)
def test_every_fixture_loads_and_satisfies_integrity(cid):
    case = load_redteam_case(cid)  # raises on schema OR id/category mismatch
    assert case.case_id == cid
    assert case.dossier.dossier_id == cid
    assert case.category == redteam_corpus.expected_category(cid)


# --------------------------------------------------------------------------------------
# Fixed inventory: the immutable category map and the observed 23/7/0 distribution
# --------------------------------------------------------------------------------------
def test_category_map_is_the_fixed_five_per_category():
    by_cat: dict[str, list[str]] = {}
    for cid in ALL_IDS:
        by_cat.setdefault(load_redteam_case(cid).category, []).append(cid)
    assert {k: len(v) for k, v in sorted(by_cat.items())} == {
        "R1": 5, "R2": 5, "R3": 5, "R4": 5, "R5": 5, "R6": 5
    }
    assert by_cat["R1"] == ALL_IDS[0:5]
    assert by_cat["R6"] == ALL_IDS[25:30]


def test_observed_action_distribution_is_23_rmi_7_escalate_0_approve():
    actions = [load_redteam_case(cid).label.appropriate_action for cid in ALL_IDS]
    assert actions.count("request_more_info") == 23
    assert actions.count("escalate") == 7
    assert actions.count("approve") == 0  # also structurally impossible via the schema
    assert len(actions) == 30


def test_escalate_cases_are_exactly_the_expected_set():
    escalate = {cid for cid in ALL_IDS if load_redteam_case(cid).label.appropriate_action == "escalate"}
    assert escalate == {"rt_06", "rt_08", "rt_09", "rt_10", "rt_11", "rt_18", "rt_20"}


def test_dependency_inventory_matches_the_pinned_dependencies():
    by_dep: dict[str, set[str]] = {}
    for cid in ALL_IDS:
        by_dep.setdefault(load_redteam_case(cid).label.external_dependency, set()).add(cid)
    assert by_dep.get("pinned_rule_parameter") == {"rt_21", "rt_22", "rt_24", "rt_25"}
    assert by_dep.get("pinned_library_behavior") == {"rt_26", "rt_27"}
    assert by_dep.get("none") == set(ALL_IDS) - {"rt_21", "rt_22", "rt_24", "rt_25", "rt_26", "rt_27"}


# --------------------------------------------------------------------------------------
# Per-case blindness: admissible under the pinned offline config (approve, no findings)
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("cid", ALL_IDS)
def test_case_is_blind_admissible_offline(cid):
    """The core P4 property: under the pinned offline stub + promoted rules, every authored
    case decides `approve` with ZERO findings of any origin. A finding here would mean the
    case is actually IN coverage — a result to investigate, never edited away."""
    dossier = load_redteam_dossier(cid)
    result = run_agent(dossier, PolicyMirrorStub(), case_ref=cid, promoted_rules=load_promoted_rules())
    assert result.decision == "approve", f"{cid} was caught: decided {result.decision}"
    assert result.findings == [], (
        f"{cid} produced findings {[(f.origin, f.code) for f in result.findings]} — it is not "
        "out-of-coverage under the pinned config"
    )


# --------------------------------------------------------------------------------------
# Behavioral disjointness from every tuning / decision corpus
# --------------------------------------------------------------------------------------
def _redteam_dossier_ids() -> set[str]:
    return {load_redteam_dossier(cid).dossier_id for cid in ALL_IDS}


def test_generator_never_produces_redteam_cases():
    from harness.generate.synthetic import generate_cases

    generated = generate_cases(per_code=1, seed=42)
    generated_ids = {gc.case.dossier.dossier_id for gc in generated}
    assert generated_ids.isdisjoint(_redteam_dossier_ids())
    assert not any(gc.case_id.startswith("rt_") for gc in generated)


def test_validation_gate_corpus_excludes_the_redteam_corpus():
    from harness.rules.gate import _known_dossiers

    gate_ids = {dossier.dossier_id for _cid, dossier in _known_dossiers(per_code=1, seed=42)}
    assert gate_ids.isdisjoint(_redteam_dossier_ids())


def test_anomaly_corpus_excludes_the_redteam_corpus():
    from harness.agent.anomaly_corpus import build_anomaly_corpus

    corpus = build_anomaly_corpus(seed=42, per_type=1)
    assert {c.dossier.dossier_id for c in corpus}.isdisjoint(_redteam_dossier_ids())


# --------------------------------------------------------------------------------------
# Corpus integrity: the hashing contract is computable and case_hash is per-case distinct
# --------------------------------------------------------------------------------------
def test_corpus_and_case_hashes_are_computable_and_distinct():
    payloads = redteam_corpus.redteam_payloads()
    assert len(payloads) == 30
    corpus_hash = hash_corpus(payloads)
    assert corpus_hash.startswith("sha256:")
    case_hashes = {cid: sha256_of(p) for cid, p in payloads}
    assert len(set(case_hashes.values())) == 30, "case hashes must be distinct per case"


# --------------------------------------------------------------------------------------
# Single-anomaly lifecycle invariant (regression guard for the rt_22/24/25 date bug)
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("cid", ALL_IDS)
def test_incorporation_precedes_all_document_dates_except_lifecycle_cases(cid):
    """Only rt_08/rt_09/rt_10 declare a document-predates-incorporation concern. For every
    other fixture, incorporation must be on or before every document date, so an R5 (or any
    other) case cannot smuggle in an unintended R2-5 temporal impossibility as a second
    anomaly — the exact defect that affected rt_22/24/25."""
    d = load_redteam_dossier(cid)
    inc = d.registry.incorporation_date
    doc_dates = [d.registry.document_date, d.circular.document_date, d.ubo.document_date]
    if cid in LIFECYCLE_ANOMALY_IDS:
        assert any(inc > dd for dd in doc_dates), (
            f"{cid} is a declared lifecycle case but no document predates incorporation"
        )
    else:
        assert all(inc <= dd for dd in doc_dates), (
            f"{cid}: incorporation {inc} is AFTER a document date {doc_dates} — an unintended "
            "second (R2-5) anomaly"
        )


# --------------------------------------------------------------------------------------
# R5 explicit boundary assertions: intended age/capital + capital-age-v1 stays silent
# --------------------------------------------------------------------------------------
def _capital_age_rule():
    rule = next(r for r in load_promoted_rules() if r.rule_id == "capital-age-v1")
    return rule, TEMPLATE_REGISTRY[rule.template_id]


@pytest.mark.parametrize("cid", sorted(R5_BOUNDARY))
def test_r5_boundary_values_and_capital_age_rule_returns_no_finding(cid):
    d = load_redteam_dossier(cid)
    reg = d.registry
    age_days = (reg.document_date - reg.incorporation_date).days
    exp = R5_BOUNDARY[cid]

    if "age_days" in exp:
        assert age_days == exp["age_days"], f"{cid}: expected age {exp['age_days']}d, got {age_days}d"
    else:
        assert age_days >= exp["age_days_min"], f"{cid}: expected age >= {exp['age_days_min']}d"
    assert reg.share_capital == exp["share_capital"]

    # The promoted capital-age-v1 rule must return NO finding, because the case sits OUTSIDE
    # its box (see `outside`). This is what makes the R5 case out-of-coverage, by construction.
    rule, template = _capital_age_rule()
    assert template.evaluate(d, rule.params) is None, (
        f"{cid} unexpectedly triggered capital-age-v1 despite being outside its box "
        f"({exp['outside']})"
    )

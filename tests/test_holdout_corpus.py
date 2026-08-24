"""P2 — the frozen holdout corpus itself: does the deterministic engine generalize to
novel, hand-authored, in-coverage cases it has never seen?

Framing that must not drift (see docs/p2_design.md):

* This is an **in-coverage** holdout. Every case's issue falls inside the deterministic
  checks' existing coverage, so this measures generalization to novel CASES, not to novel
  PHENOMENA. Out-of-coverage generalization is P4's job.
* ``false_approval_rate == 0`` here is an **observed, corpus-scoped, empirical** result.
  It is asserted because it is the headline safety metric and a regression must fail
  loudly — but it is NOT a structural guarantee and must never be reported as one. The
  only structural invariant is SYSTEM-origin-implies-no-approve
  (``tests/test_system_errors.py``), which is deliberately not restated as a rate.
"""

from __future__ import annotations

import pytest

from harness.agent.runner import run_agent
from harness.data.holdout_corpus import (
    list_holdout_ids,
    load_holdout_case,
    load_holdout_dossier,
)
from harness.eval.metrics import (
    agent_finding_codes,
    decision_match,
    finding_match,
    trajectory_match,
)
from harness.llm.stub import PolicyMirrorStub
from harness.rules.store import load_promoted_rules

# The 14 taxonomy codes the corpus must exercise (NONE is the "clean" label).
ALL_TAXONOMY_CODES = {
    "NONE", "A1", "A2", "B1a", "B1b", "B2a", "B2b",
    "C1a", "C1b", "C2", "D1a", "D1b", "E1", "E2",
}

EXPECTED_CASE_COUNT = 18
EXPECTED_DECISION_CLASS_COUNTS = {"approve": 4, "request_more_info": 8, "escalate": 6}


def _screen(case_id: str):
    """Screen one holdout case exactly as the holdout report will: offline stub, and the
    REAL promoted-rule store (so the corpus reflects actual current coverage)."""
    dossier = load_holdout_dossier(case_id)
    return run_agent(
        dossier, PolicyMirrorStub(), case_ref=case_id, promoted_rules=load_promoted_rules()
    )


# --------------------------------------------------------------------------------------
# The coverage matrix (asserted as a contract, not left to inspection)
# --------------------------------------------------------------------------------------
def test_holdout_has_the_expected_case_count():
    assert len(list_holdout_ids()) == EXPECTED_CASE_COUNT


def test_holdout_ids_are_contiguous_and_canonical():
    assert list_holdout_ids() == [f"hold_{i:02d}" for i in range(1, EXPECTED_CASE_COUNT + 1)]


def test_holdout_covers_every_taxonomy_code():
    covered = set()
    for case_id in list_holdout_ids():
        covered.update(load_holdout_case(case_id).decision_truth.injected_codes)
    assert covered == ALL_TAXONOMY_CODES


def test_holdout_decision_class_balance():
    """All three decision classes are represented, with the middle class (the one both a
    lax and an over-cautious agent get wrong) the largest."""
    counts: dict[str, int] = {}
    for case_id in list_holdout_ids():
        cls = load_holdout_case(case_id).decision_truth.expected_decision
        counts[cls] = counts.get(cls, 0) + 1
    assert counts == EXPECTED_DECISION_CLASS_COUNTS


def test_holdout_contains_compound_cases():
    """At least two cases carry more than one code — shapes the single-injector generator
    structurally cannot produce, so the guardrail's max-severity composition is exercised
    on unseen data rather than only in the P3 compound suite."""
    compound = [
        cid
        for cid in list_holdout_ids()
        if len(load_holdout_case(cid).decision_truth.injected_codes) > 1
    ]
    assert len(compound) >= 2


# --------------------------------------------------------------------------------------
# Per-case correctness
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("case_id", list_holdout_ids())
def test_holdout_case_decides_correctly(case_id: str):
    truth = load_holdout_case(case_id).decision_truth
    result = _screen(case_id)
    assert decision_match(result, truth), (
        f"{case_id}: expected {truth.expected_decision}, got {result.decision}"
    )


@pytest.mark.parametrize("case_id", list_holdout_ids())
def test_holdout_case_finds_the_right_codes(case_id: str):
    """Right decision for the RIGHT REASON — exact code-set equality, so a case that
    decides correctly via an unrelated finding still fails."""
    truth = load_holdout_case(case_id).decision_truth
    result = _screen(case_id)
    assert finding_match(result, truth), (
        f"{case_id}: expected codes {sorted(truth.injected_codes)}, "
        f"got {sorted(agent_finding_codes(result))}"
    )


@pytest.mark.parametrize("case_id", list_holdout_ids())
def test_holdout_case_trajectory_is_correct(case_id: str):
    truth = load_holdout_case(case_id).decision_truth
    result = _screen(case_id)
    assert trajectory_match(result, truth), (
        f"{case_id}: trajectory {result.trajectory}, skipped {result.skipped_checks}"
    )


# --------------------------------------------------------------------------------------
# Headline safety metric (empirical, corpus-scoped)
# --------------------------------------------------------------------------------------
def test_no_false_approval_on_the_holdout():
    """OBSERVED false_approval_rate == 0 on this corpus.

    Empirical and corpus-scoped: it holds because the deterministic checks actually catch
    each authored case, not because approval is structurally impossible. Reported with its
    raw k/n; a Wilson CI accompanies it in the P5 holdout artifact (commit b).
    """
    false_approvals = []
    non_approve = 0
    for case_id in list_holdout_ids():
        truth = load_holdout_case(case_id).decision_truth
        if truth.expected_decision == "approve":
            continue
        non_approve += 1
        if _screen(case_id).decision == "approve":
            false_approvals.append(case_id)

    assert non_approve == EXPECTED_CASE_COUNT - EXPECTED_DECISION_CLASS_COUNTS["approve"]
    assert false_approvals == [], f"false approvals on the holdout: {false_approvals}"


def test_no_system_findings_on_the_offline_holdout_run():
    """The offline stubs must not produce T-family findings. If they did, domain metrics on
    this corpus would be silently measuring infrastructure noise."""
    for case_id in list_holdout_ids():
        result = _screen(case_id)
        assert result.system_failure_count == 0, f"{case_id}: {result.system_error_codes}"


# --------------------------------------------------------------------------------------
# hold_15 — the case whose construction was the one open design question
# --------------------------------------------------------------------------------------
def test_hold_15_isolates_e2_exactly():
    """hold_15's label must be E2 and ONLY E2.

    The fixture blanks ``legal_name`` consistently across all three documents. That keeps
    ``check_identity_consistency``'s surface set at size 1, so the A1 branch — and with it
    the rapidfuzz comparison — is never reached: the case's ground truth does not depend on
    a third-party matcher's empty-string edge behavior. Identical tax_ids rule out A2.
    """
    case = load_holdout_case("hold_15")
    result = _screen("hold_15")

    assert agent_finding_codes(result) == {"E2"}
    assert set(case.decision_truth.injected_codes) == {"E2"}
    assert result.decision == "request_more_info"

    codes = {f.code for f in result.findings}
    assert "A1" not in codes, "blank legal_name must not reach the A1 surface-variance branch"
    assert "A2" not in codes, "tax_ids are identical; no identity conflict is possible"
    assert "F1" not in codes, "the company is years old; capital_age_ceiling cannot fire"

    e2 = [f for f in result.findings if f.code == "E2"]
    assert len(e2) == 1
    assert "registry.legal_name" in e2[0].detail


def test_hold_12_does_not_flag_the_sub_threshold_shareholder():
    """False-positive guard: a shareholder below UBO_THRESHOLD_PCT is legitimately absent
    from declared_ubo. hold_12 carries a real D1a AND a sub-threshold holder, so a check
    that flagged both would still 'decide correctly' while being wrong."""
    result = _screen("hold_12")
    assert agent_finding_codes(result) == {"D1a"}
    d1a = [f for f in result.findings if f.code == "D1a"]
    assert len(d1a) == 1, "only the >=25% undeclared holder may be flagged"
    assert "Sedat Bilgehan" not in d1a[0].detail

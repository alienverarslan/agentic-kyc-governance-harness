"""P2 commit (b): the deterministic_holdout report.

The tests here protect one property above all others: **the holdout artifact and the
development artifact never merge.** A single averaged headline number would destroy the
only thing P2 buys — a measurement taken on cases the engine was never tuned against — by
diluting 18 held-out cases into a 420-case in-sample corpus.

Everything statistical is P5's, already tested in ``tests/test_scientific_report.py`` and
``tests/test_stats.py``. These tests cover what this module actually contributes: corpus
selection, the integrity guard, labelling, and separation.
"""

from __future__ import annotations

import json

import pytest

from harness.data.holdout_corpus import list_holdout_ids, load_manifest
from harness.eval import holdout_report
from harness.eval.scientific_report import SCHEMA_VERSION
from harness.rules.store import load_promoted_rules


@pytest.fixture(scope="module")
def report():
    """Built once — it screens 18 cases twice (with and without promoted rules)."""
    return holdout_report.build_holdout_report()


# --------------------------------------------------------------------------------------
# Separation from the development report
# --------------------------------------------------------------------------------------
def test_run_type_is_distinct_from_the_development_report(report):
    assert report["run"]["run_type"] == "deterministic_holdout"
    assert report["run"]["run_type"] != "deterministic_offline"


def test_corpus_is_labelled_out_of_sample(report):
    corpus = report["corpus"]
    assert corpus["sample_relationship"] == "out_of_sample"
    assert corpus["data_class"] == "synthetic"
    assert corpus["authoring"] == "hand_authored"
    assert corpus["coverage_scope"] == "in_coverage"


def test_the_development_report_is_labelled_in_sample():
    """The other half of the labelling contract: if the dev corpus were unlabelled, a
    reader comparing two artifacts could not tell which numbers were tuned against."""
    from harness.eval.scientific_report import build_offline_report

    dev = build_offline_report(per_code=1, seed=42)
    assert dev["corpus"]["sample_relationship"] == "in_sample"
    assert dev["run"]["run_type"] == "deterministic_offline"


def test_the_two_reports_write_to_different_paths():
    """Separation is enforced by construction, not by operator discipline."""
    from harness.eval import scientific_report

    assert holdout_report.DEFAULT_OUT_DIR != "artifacts/p5"
    assert "holdout" in holdout_report.DEFAULT_OUT_DIR
    assert scientific_report.main is not holdout_report.main


def test_holdout_report_contains_no_generator_corpus_data(report):
    """A merged or contaminated artifact would show generator case ids or a generator seed."""
    corpus = report["corpus"]
    assert corpus["name"] == "deterministic_holdout"
    assert "seed" not in corpus, "a generator seed has no meaning for a hand-authored corpus"
    assert "per_code" not in corpus
    case_ids = {
        c for surface in [report["metrics"]["deterministic_engine"]] for c in surface.get("by_taxonomy_code", {})
    }
    assert case_ids  # sanity: the surface is populated
    assert corpus["case_count"] == len(list_holdout_ids()) == 18


# --------------------------------------------------------------------------------------
# Integrity guard (fail-closed, no artifact)
# --------------------------------------------------------------------------------------
def test_report_verifies_the_corpus_against_its_frozen_manifest(report):
    assert report["corpus"]["corpus_hash"] == load_manifest()["corpus_hash"]
    assert report["corpus"]["corpus_version"] == load_manifest()["corpus_version"]


def test_a_drifted_corpus_raises_and_produces_no_report(monkeypatch):
    """If the live corpus no longer hashes to its pin, the report must refuse rather than
    publish numbers under a hash that no longer describes what was screened."""
    monkeypatch.setattr(
        holdout_report.provenance, "hash_corpus", lambda payloads: "sha256:" + "e" * 64
    )
    with pytest.raises(holdout_report.HoldoutIntegrityError) as exc:
        holdout_report.build_holdout_report()
    assert "does not match its frozen manifest" in str(exc.value)


def test_a_case_set_mismatch_raises(monkeypatch):
    monkeypatch.setattr(holdout_report, "list_holdout_ids", lambda: ["hold_01"])
    with pytest.raises(holdout_report.HoldoutIntegrityError):
        holdout_report.build_holdout_report()


def test_main_writes_neither_json_nor_markdown_when_the_corpus_has_drifted(
    tmp_path, monkeypatch
):
    """Fail-closed at the CLI boundary, not just in the builder: an integrity failure must
    leave NO artifact behind — not a partial JSON, not a Markdown file, not even the output
    directory."""
    out_dir = tmp_path / "p2_holdout"
    monkeypatch.setattr(
        holdout_report.provenance, "hash_corpus", lambda payloads: "sha256:" + "e" * 64
    )
    monkeypatch.setattr("sys.argv", ["holdout_report", "--out", str(out_dir)])

    with pytest.raises(holdout_report.HoldoutIntegrityError):
        holdout_report.main()

    assert not out_dir.exists(), "an artifact directory was created despite corpus drift"


# --------------------------------------------------------------------------------------
# P5 conventions are reused verbatim, not reimplemented
# --------------------------------------------------------------------------------------
def test_report_uses_the_shared_schema_version(report):
    assert report["schema_version"] == SCHEMA_VERSION


def test_every_published_rate_carries_counts_and_a_wilson_interval(report):
    """House style: no bare rates anywhere in the artifact."""
    det = report["metrics"]["deterministic_engine"]
    safety = report["metrics"]["final_guardrail_safety"]
    operational = report["metrics"]["operational_reliability"]

    rates = [
        det["decision_accuracy"],
        det["finding_accuracy"],
        safety["false_approval_rate"],
        safety["guardrail_override_rate"],
        operational["system_failure_rate"],
    ]
    for stat in rates:
        for key in ("k", "n", "point_estimate", "wilson_ci_95"):
            assert key in stat, f"published rate missing {key!r}: {stat}"


def test_holdout_results_are_perfect_on_the_frozen_corpus(report):
    det = report["metrics"]["deterministic_engine"]
    assert det["decision_accuracy"]["k"] == det["decision_accuracy"]["n"] == 18
    assert det["finding_accuracy"]["k"] == det["finding_accuracy"]["n"] == 18


def test_false_approval_rate_is_zero_with_its_interval_and_empirical_framing(report):
    """0/14 published WITH its interval and its scope qualifier, never bare."""
    safety = report["metrics"]["final_guardrail_safety"]
    far = safety["false_approval_rate"]
    assert far["k"] == 0
    assert far["n"] == 14
    lo, hi = far["wilson_ci_95"]
    assert lo == pytest.approx(0.0, abs=1e-12)
    assert hi > 0.01, "a near-zero-width CI at k=0 would overstate 14 observations"

    note = safety["holdout_far_interpretation"].lower()
    assert "empirical" in note
    assert "not a structural guarantee" in note


def test_system_invariant_is_reported_as_structural_not_as_a_rate(report):
    """The empirical/structural distinction, asserted where it is published."""
    inv = report["metrics"]["final_guardrail_safety"]["system_present_implies_no_approve"]
    assert inv["type"] == "structural_invariant"
    assert "wilson_ci_95" not in inv
    assert "point_estimate" not in inv
    assert inv["verified_by"].startswith("tests/test_system_errors.py")


def test_learned_rule_marginal_is_a_paired_diff_with_no_regressions(report):
    """The promoted capital-age rule cannot fire on this corpus (every company is years
    old), so its marginal contribution here is legitimately zero — reported as a paired
    diff, never as recall."""
    lr = report["metrics"]["deterministic_engine"]["learned_rule_marginal"]
    assert lr["evaluated_cases"] == 18
    assert lr["newly_incorrect_due_to_rule"] == 0
    assert lr["promoted_rule_ids"] == sorted(r.rule_id for r in load_promoted_rules())
    assert "marginal_gain_rate" in lr


def test_zero_rule_exposure_is_published_and_not_read_as_rule_validation(report):
    """A zero paired delta from a rule that never fired means NOT EXERCISED.

    Without an explicit exposure count, `net_correctness_delta: 0` invites the reading "the
    promoted rule was validated out-of-sample", which this corpus cannot support: it simply
    contains no case the capital-age rule applies to.
    """
    lr = report["metrics"]["deterministic_engine"]["learned_rule_marginal"]
    assert lr["rule_applicability_count"] == 0
    assert lr["newly_correct_due_to_rule"] == 0
    assert lr["net_correctness_delta"] == 0

    note = lr["exposure_note"].lower()
    assert "not exercised" in note
    assert "not evidence of out-of-sample rule safety or benefit" in note


def test_exposure_caveat_appears_beside_the_paired_diff_in_markdown(report):
    """Rendered adjacent to the delta, not buried in a header — a caveat separated from the
    number it qualifies does not travel with it."""
    md = holdout_report.render_markdown(report)
    assert "rule_applicability_count: 0" in md
    idx_section = md.index("Learned-rule (F1) marginal contribution")
    idx_exposure = md.index("**exposure:**")
    idx_next = md.index("## 2. AI triage incremental recall")
    assert idx_section < idx_exposure < idx_next


def test_no_system_findings_on_the_offline_holdout_run(report):
    op = report["metrics"]["operational_reliability"]
    assert op["system_failure_rate"]["k"] == 0
    assert op["error_kind_distribution"] == {}


# --------------------------------------------------------------------------------------
# Rendering carries the qualifiers before the numbers
# --------------------------------------------------------------------------------------
def test_markdown_states_scope_before_results(report):
    md = holdout_report.render_markdown(report)
    assert "OUT-OF-SAMPLE" in md
    assert "not the development report" in md.lower()
    assert "not a blinded external benchmark" in md.lower()
    # The scope header must precede the first metrics section.
    assert md.index("Scope.") < md.index("## 1. Deterministic engine correctness")


def test_holdout_markdown_has_exactly_one_h1_and_it_is_the_holdout_title(report):
    """A P2 artifact must carry ONE identity. The prior version emitted both a 'P2 Holdout
    Report' H1 and P5's 'P5 Scientific Report' H1, which made a held-out evaluation artifact
    look partly like the in-sample development report."""
    md = holdout_report.render_markdown(report)
    h1s = [line for line in md.splitlines() if line.startswith("# ")]
    assert len(h1s) == 1, f"expected exactly one H1, got {h1s}"
    assert h1s[0] == "# P2 Holdout Report — deterministic_holdout (OUT-OF-SAMPLE)"
    assert "# P5 Scientific Report" not in md


def test_the_development_report_keeps_its_p5_title_unchanged():
    """The override must not leak into the development report: omitting `title` preserves
    the P5 heading exactly."""
    from harness.eval.scientific_report import build_offline_report, render_markdown

    dev_md = render_markdown(build_offline_report(per_code=1, seed=42))
    h1s = [line for line in dev_md.splitlines() if line.startswith("# ")]
    assert h1s == ["# P5 Scientific Report — doc-consistency-governance-harness"]


def test_report_is_json_serializable(report):
    """The JSON artifact is the source of truth; a non-serializable field would surface
    only at write time, after the expensive screening passes."""
    json.dumps(report, ensure_ascii=False)

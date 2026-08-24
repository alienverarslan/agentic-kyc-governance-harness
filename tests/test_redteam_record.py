"""P4(b) — the ``redteam_admission_record`` producer: an offline provenance RECORD, never a
scientific report, result, score, or performance artifact.

Two corpus sources are used, deliberately:

* **The REAL, already-frozen 30-case corpus** — for every test about record CONTENT (schema,
  the sole ``30/30`` literal, no-metrics enforcement, per-case rows, the two-Git-SHA
  separation). These call ``build_admission_record()`` / ``render_markdown()`` directly and
  NEVER call ``publish()`` against the real path, so no artifact is ever written to the repo.
* **A tiny SANDBOXED corpus** (mirroring ``test_freeze_redteam.py``'s pattern: a real freeze
  run against a synthetic 2-case corpus under ``tmp_path``) — for every test about the
  PUBLICATION LIFECYCLE and the eleven pre-write integrity checks, where writing files and
  deliberately corrupting a manifest are the point. All such writes land under ``tmp_path``.

No test in this module ever writes to ``artifacts/p4_redteam`` on the real filesystem.
"""

from __future__ import annotations

import copy
import json
from random import Random

import pytest

from harness.data import freeze_redteam, redteam_corpus
from harness.eval import redteam_record
from harness.generate.synthetic import generate_for_code
from harness.rules.schema import CandidateRule


# ========================================================================================
# Sandbox: a tiny, real, self-consistent frozen corpus under tmp_path
# ========================================================================================
def _clean_dossier(case_id: str):
    d = generate_for_code("NONE", 1, Random(1))[0].case.dossier.model_copy(deep=True)
    d.dossier_id = case_id
    return d


def _label(action="request_more_info", ext_dep="none"):
    return {
        "appropriate_action": action,
        "label_basis": "threat_model_judgment",
        "label_review": "human_approved",
        "authoring_assistance": "llm_assisted",
        "self_contained_evidence": "synthetic self-contained fact",
        "out_of_coverage_rationale": "outside every deterministic check's scope",
        "external_dependency": ext_dep,
    }


def _write_case(dirpath, case_id, dossier, action="request_more_info", ext_dep="none"):
    payload = {
        "case_id": case_id,
        "category": redteam_corpus.expected_category(case_id),
        "dossier": dossier.model_dump(mode="json"),
        "label": _label(action=action, ext_dep=ext_dep),
    }
    (dirpath / f"case_{case_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A real, frozen, 2-case corpus under tmp_path. Redirects redteam_corpus/freeze_redteam
    module paths and EXPECTED_CASE_COUNT, writes 2 clean synthetic fixtures, and runs the
    REAL freeze tool — so the resulting manifest is genuinely self-consistent (correct
    hashes, correct pinned_config from whatever is actually promoted right now), exactly as
    test_freeze_redteam.py's `frozen` fixture does."""
    corpus = tmp_path / "redteam"
    corpus.mkdir()
    manifest_path = corpus / "manifest.json"
    monkeypatch.setattr(redteam_corpus, "REDTEAM_DIR", corpus)
    monkeypatch.setattr(redteam_corpus, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(freeze_redteam, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(freeze_redteam, "EXPECTED_CASE_COUNT", 2)
    monkeypatch.setattr(redteam_corpus, "EXPECTED_CASE_COUNT", 2)

    _write_case(corpus, "rt_01", _clean_dossier("rt_01"), ext_dep="pinned_library_behavior")
    _write_case(corpus, "rt_02", _clean_dossier("rt_02"))
    freeze_redteam.main([])

    return corpus, manifest_path


def _read_manifest(manifest_path):
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _write_manifest(manifest_path, data):
    manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _out_root(tmp_path):
    return tmp_path / "artifacts" / "p4_redteam"


def _nothing_was_created(tmp_path):
    root = tmp_path / "artifacts"
    return not root.exists() or list(root.rglob("*")) == []


# ========================================================================================
# Group 1 — naming, schema, no-agent (against the REAL frozen corpus; no publish)
# ========================================================================================
def test_record_declares_the_p4b_schema_run_type_and_filenames():
    record = redteam_record.build_admission_record()
    assert record["schema"] == "redteam_admission_record"
    assert record["schema_version"] == "1.0.0"
    assert record["run"]["run_type"] == "redteam_out_of_coverage"
    assert redteam_record.JSON_FILENAME == "redteam_admission_record.json"
    assert redteam_record.MD_FILENAME == "redteam_admission_record.md"


def test_public_names_avoid_report_score_results_performance():
    forbidden = ("report", "score", "results", "performance")
    for name in (
        redteam_record.RECORD_SCHEMA,
        redteam_record.JSON_FILENAME,
        redteam_record.MD_FILENAME,
        redteam_record.__name__,
    ):
        low = name.lower()
        for word in forbidden:
            assert word not in low, f"{name!r} contains forbidden word {word!r}"


def test_record_module_never_imports_or_calls_the_agent():
    import inspect

    source = inspect.getsource(redteam_record)
    for token in ("run_agent", "import runner", "RunRecord", "build_scientific_report"):
        assert token not in source, f"redteam_record.py must never reference {token!r}"


# ========================================================================================
# Group 2 — corpus identity block, challenge-set language (REAL corpus)
# ========================================================================================
def test_corpus_block_carries_identity_and_frozen_git_provenance():
    record = redteam_record.build_admission_record()
    corpus = record["corpus"]
    assert corpus["corpus_hash"].startswith("sha256:")
    assert corpus["case_count"] == 30
    assert set(corpus["frozen_git"]) == {"head_commit_sha", "branch", "worktree_dirty"}
    assert corpus["data_class"] == "synthetic"
    assert corpus["authoring"] == "hand_authored"
    assert corpus["authoring_context"] == "author_constructed_non_blinded"
    assert corpus["corpus_relationship"] == "fixed_synthetic_challenge_set"
    assert corpus["coverage_scope"] == "out_of_coverage"


def test_corpus_block_never_uses_sample_relationship_or_out_of_sample_language():
    record = redteam_record.build_admission_record()
    md = redteam_record.render_markdown(record)
    dumped = json.dumps(record)
    for forbidden in ("sample_relationship", "out_of_sample"):
        assert forbidden not in dumped
        assert forbidden not in md


# ========================================================================================
# Group 3 — admission block and the sole "30/30" literal
# ========================================================================================
def test_admission_block_has_exactly_the_seven_expected_keys():
    record = redteam_record.build_admission_record()
    assert set(record["admission"]) == {
        "criterion", "result", "basis", "interpretation",
        "is_empirical_rate", "confidence_interval", "ci_omission_reason",
    }


def test_admission_is_by_construction_with_no_rate_or_ci():
    admission = redteam_record.build_admission_record()["admission"]
    assert admission["result"] == "30/30"
    assert admission["basis"] == "by_construction"
    assert admission["is_empirical_rate"] is False
    assert admission["confidence_interval"] is None
    assert admission["ci_omission_reason"]


def test_json_contains_the_literal_30_of_30_exactly_once_at_admission_result():
    record = redteam_record.build_admission_record()
    dumped = json.dumps(record)
    assert dumped.count('"30/30"') == 1
    assert record["admission"]["result"] == "30/30"


def test_markdown_contains_the_literal_30_of_30_exactly_once_in_the_admission_section():
    record = redteam_record.build_admission_record()
    md = redteam_record.render_markdown(record)
    lines = md.splitlines()
    hits = [i for i, line in enumerate(lines) if "30/30" in line]
    assert len(hits) == 1
    admission_start = next(i for i, l in enumerate(lines) if l.strip() == "## Admission")
    next_h2 = next(
        (i for i in range(admission_start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    assert admission_start < hits[0] < next_h2


def test_limits_state_the_boundary_without_the_literal_30_of_30():
    record = redteam_record.build_admission_record()
    limits_text = " ".join(record["limits"])
    assert "30/30" not in limits_text
    assert "by-construction property" in limits_text
    assert "false-approval rate" in limits_text
    assert "no confidence interval" in limits_text


def test_markdown_places_interpretation_and_ci_omission_immediately_after_the_admission_line():
    record = redteam_record.build_admission_record()
    md = redteam_record.render_markdown(record)
    lines = [l for l in md.splitlines() if l.strip()]
    idx = next(i for i, l in enumerate(lines) if "30/30" in l)
    window = " ".join(lines[idx : idx + 4])
    assert record["admission"]["interpretation"] in window
    assert record["admission"]["ci_omission_reason"] in window


# ========================================================================================
# Group 4 — no-metrics enforcement: allowlist inside `admission`, forbidden elsewhere
# ========================================================================================
_FORBIDDEN_METRIC_KEYS = {
    "false_approval_rate", "wilson_ci_95", "point_estimate", "accuracy",
    "recall", "precision", "k", "n", "far",
}


def _walk(obj, path=()):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield path + (key,), key
            yield from _walk(value, path + (key,))
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item, path)


def test_no_forbidden_metric_keys_outside_the_admission_qualifiers():
    record = redteam_record.build_admission_record()
    for path, key in _walk(record):
        if key in _FORBIDDEN_METRIC_KEYS or key.endswith("_rate") or "_ci" in key.lower():
            assert path[:-1] == ("admission",) or key in {
                "is_empirical_rate", "confidence_interval", "ci_omission_reason",
            }, f"forbidden metric-shaped key {key!r} found outside admission at {path}"


def test_inventory_contains_no_float_values_and_carries_its_note():
    inventory = redteam_record.build_admission_record()["inventory"]
    assert inventory["inventory_note"]

    def _no_floats(obj):
        if isinstance(obj, float):
            raise AssertionError(f"float found in inventory: {obj!r}")
        if isinstance(obj, dict):
            for v in obj.values():
                _no_floats(v)
        if isinstance(obj, list):
            for v in obj:
                _no_floats(v)
    _no_floats(inventory)


def test_markdown_contains_no_percentage_or_stray_kn_framing():
    import re

    record = redteam_record.build_admission_record()
    md = redteam_record.render_markdown(record)
    assert not re.search(r"\d+(\.\d+)?\s*%", md)
    for match in re.finditer(r"\b\d+\s*/\s*\d+\b", md):
        assert match.group(0) == "30/30", f"unexpected k/n-shaped text: {match.group(0)!r}"


def test_markdown_mentions_wilson_only_inside_the_ci_omission_sentence():
    """The by-construction analogy legitimately names Wilson CI (the same analogy used for
    SYSTEM-implies-no-approve throughout the project); it must never appear as though this
    record itself reports or claims one.

    Exact occurrence/containment check, not a fixed-size surrounding window: a window can
    match by proximity without the sole occurrence actually being part of the complete
    rendered ci_omission_reason sentence. The rule is that the one rendered "Wilson" must sit
    inside the complete rendered ci_omission_reason, not merely somewhere near it."""
    record = redteam_record.build_admission_record()
    md = redteam_record.render_markdown(record)
    ci_reason = record["admission"]["ci_omission_reason"]
    assert "Wilson" in ci_reason
    assert md.count("Wilson") == 1
    assert ci_reason in md


def test_markdown_mentions_confidence_interval_only_in_the_three_permitted_places():
    """Exact occurrence/location check, not a fixed-size surrounding window: a window can
    truncate a long sentence and misjudge a legitimate occurrence near its start or end.

    Three sentences legitimately carry the phrase, all non-claims about the ABSENCE of a
    confidence interval, never a claim of one:
    * `admission.interpretation` — copied VERBATIM from the frozen manifest (not authored
      by this record), already-reviewed text stating the by-construction admission carries
      no confidence interval. Required content: the admission-placement test above already
      requires it to be rendered immediately after the sole "30/30".
    * `admission.ci_omission_reason` — this record's own by-construction/Wilson-CI analogy.
    * The Limits entry restating the same boundary for a reader who only reads Limits.

    All three must be present verbatim, and the phrase must occur exactly three times in
    the whole document — once per sentence, nowhere else."""
    record = redteam_record.build_admission_record()
    md = redteam_record.render_markdown(record)
    interpretation = record["admission"]["interpretation"]
    ci_reason = record["admission"]["ci_omission_reason"]
    limits_entry = next(l for l in record["limits"] if "confidence interval" in l)
    assert "confidence interval" in interpretation
    assert "confidence interval" in ci_reason
    assert "confidence interval" in limits_entry
    assert interpretation in md
    assert ci_reason in md
    assert limits_entry in md
    assert md.count("confidence interval") == 3


def test_markdown_uses_performance_and_effectiveness_only_inside_limits():
    record = redteam_record.build_admission_record()
    md = redteam_record.render_markdown(record)
    limits_start = md.index("## Limits")
    limits_end = md.index("## Corpus identity")
    limits_region = md[limits_start:limits_end]
    rest = md[:limits_start] + md[limits_end:]
    for word in ("performance", "effectiveness", "score", "accuracy", "recall", "precision"):
        assert word not in rest.lower(), f"{word!r} appears outside the Limits section"
    assert "performance" in limits_region.lower()


def test_limits_requires_the_not_a_performance_measurement_non_claim():
    limits = redteam_record.build_admission_record()["limits"]
    assert any("not a" in l.lower() and "performance measurement" in l.lower() for l in limits)


# ========================================================================================
# Group 5 — per-case content (REAL corpus, parametrized over all 30 ids)
# ========================================================================================
ALL_REAL_IDS = [f"rt_{i:02d}" for i in range(1, 31)]


def test_per_case_has_thirty_rows_matching_the_manifest():
    record = redteam_record.build_admission_record()
    assert [row["case_id"] for row in record["per_case"]] == ALL_REAL_IDS


@pytest.mark.parametrize("case_id", ALL_REAL_IDS)
def test_per_case_row_copies_pinned_facts_and_case_hash(case_id):
    record = redteam_record.build_admission_record()
    manifest = redteam_corpus.load_manifest()
    row = next(r for r in record["per_case"] if r["case_id"] == case_id)
    entry = manifest["per_case"][case_id]
    assert row["category"] == entry["category"]
    assert row["appropriate_action"] == entry["appropriate_action"]
    assert row["external_dependency"] == entry["external_dependency"]
    assert row["out_of_coverage_rationale"] == entry["out_of_coverage_rationale"]
    assert row["case_hash"] == entry["case_hash"]


@pytest.mark.parametrize("case_id", ALL_REAL_IDS)
def test_per_case_observation_is_marked_historical_and_not_reobserved(case_id):
    record = redteam_record.build_admission_record()
    manifest = redteam_corpus.load_manifest()
    row = next(r for r in record["per_case"] if r["case_id"] == case_id)
    assert row["observation_kind"] == "historical_at_freeze"
    assert row["observation_reobserved_for_this_record"] is False
    assert row["observed_at_freeze"] == manifest["per_case"][case_id]["observed_at_freeze"]


@pytest.mark.parametrize("case_id", ALL_REAL_IDS)
def test_per_case_entries_carry_no_invented_result_summary_field(case_id):
    record = redteam_record.build_admission_record()
    row = next(r for r in record["per_case"] if r["case_id"] == case_id)
    assert set(row) == {
        "case_id", "category", "appropriate_action", "external_dependency",
        "out_of_coverage_rationale", "case_hash", "observation_kind",
        "observation_reobserved_for_this_record", "observed_at_freeze",
    }


# ========================================================================================
# Group 6 — two Git SHAs kept in separate sections with separate notes (REAL corpus)
# ========================================================================================
def test_corpus_identity_section_carries_anchor_and_bootstrap_notes_only():
    record = redteam_record.build_admission_record()
    md = redteam_record.render_markdown(record)
    section = md[md.index("## Corpus identity") : md.index("## Pinned configuration")]
    assert record["provenance_notes"]["anchor_note"] in section
    assert record["provenance_notes"]["frozen_bootstrap_note"] in section
    assert record["provenance_notes"]["publication_commit_note"] not in section
    assert record["run"]["git"]["commit_sha"] not in section


def test_publication_provenance_section_carries_the_publication_note_only():
    record = redteam_record.build_admission_record()
    md = redteam_record.render_markdown(record)
    section = md[md.index("## Publication provenance") :]
    assert record["provenance_notes"]["publication_commit_note"] in section
    assert record["provenance_notes"]["frozen_bootstrap_note"] not in section
    assert record["corpus"]["frozen_git"]["head_commit_sha"] not in section


def test_corpus_hash_is_explicitly_identified_as_the_sole_anchor():
    record = redteam_record.build_admission_record()
    anchor_note = record["provenance_notes"]["anchor_note"]
    assert "corpus_hash" in anchor_note
    assert "sole corpus-content reproducibility anchor" in anchor_note


# ========================================================================================
# Group 7 — the validity predicate: exactly two regular files, no other entries
# ========================================================================================
def test_valid_publication_requires_exactly_two_regular_expected_files(tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    (good / redteam_record.JSON_FILENAME).write_text("{}", encoding="utf-8")
    (good / redteam_record.MD_FILENAME).write_text("#", encoding="utf-8")
    assert redteam_record._is_valid_record_directory(good) is True

    empty = tmp_path / "empty"
    empty.mkdir()
    assert redteam_record._is_valid_record_directory(empty) is False

    json_only = tmp_path / "json_only"
    json_only.mkdir()
    (json_only / redteam_record.JSON_FILENAME).write_text("{}", encoding="utf-8")
    assert redteam_record._is_valid_record_directory(json_only) is False

    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / redteam_record.JSON_FILENAME).write_text("{}", encoding="utf-8")
    (extra / redteam_record.MD_FILENAME).write_text("#", encoding="utf-8")
    (extra / "stray.txt").write_text("x", encoding="utf-8")
    assert redteam_record._is_valid_record_directory(extra) is False

    json_is_dir = tmp_path / "json_is_dir"
    json_is_dir.mkdir()
    (json_is_dir / redteam_record.JSON_FILENAME).mkdir()
    (json_is_dir / redteam_record.MD_FILENAME).write_text("#", encoding="utf-8")
    assert redteam_record._is_valid_record_directory(json_is_dir) is False

    try:
        symlinked = tmp_path / "symlinked"
        symlinked.mkdir()
        (symlinked / redteam_record.JSON_FILENAME).write_text("{}", encoding="utf-8")
        target = tmp_path / "md_target.md"
        target.write_text("#", encoding="utf-8")
        (symlinked / redteam_record.MD_FILENAME).symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform/user")
    else:
        assert redteam_record._is_valid_record_directory(symlinked) is False


# ========================================================================================
# Group 8 — publication lifecycle (sandboxed corpus, tmp_path output)
# ========================================================================================
def test_run_id_matches_the_output_directory_name(sandbox, tmp_path):
    final = redteam_record.publish(out_dir=_out_root(tmp_path))
    record = json.loads((final / redteam_record.JSON_FILENAME).read_text(encoding="utf-8"))
    assert final.name == record["run"]["run_id"]


def test_two_publications_coexist_as_siblings_with_identical_corpus_hash(sandbox, monkeypatch, tmp_path):
    ids = iter(["20260101T000000Z-redteam", "20260101T000001Z-redteam"])
    monkeypatch.setattr(redteam_record, "_generate_run_id", lambda: next(ids))
    first = redteam_record.publish(out_dir=_out_root(tmp_path))
    second = redteam_record.publish(out_dir=_out_root(tmp_path))
    assert first != second
    assert first.exists() and second.exists()
    r1 = json.loads((first / redteam_record.JSON_FILENAME).read_text(encoding="utf-8"))
    r2 = json.loads((second / redteam_record.JSON_FILENAME).read_text(encoding="utf-8"))
    assert r1["corpus"]["corpus_hash"] == r2["corpus"]["corpus_hash"]
    assert r1["run"]["run_id"] != r2["run"]["run_id"]


def test_existing_run_directory_refuses_publication(sandbox, monkeypatch, tmp_path):
    monkeypatch.setattr(redteam_record, "_generate_run_id", lambda: "fixed-run-id")
    first = redteam_record.publish(out_dir=_out_root(tmp_path))
    before = {
        (first / redteam_record.JSON_FILENAME).read_text(encoding="utf-8"),
        (first / redteam_record.MD_FILENAME).read_text(encoding="utf-8"),
    }
    with pytest.raises(redteam_record.RecordCollisionError):
        redteam_record.publish(out_dir=_out_root(tmp_path))
    after = {
        (first / redteam_record.JSON_FILENAME).read_text(encoding="utf-8"),
        (first / redteam_record.MD_FILENAME).read_text(encoding="utf-8"),
    }
    assert before == after


def test_collision_refused_against_an_empty_existing_directory(sandbox, monkeypatch, tmp_path):
    monkeypatch.setattr(redteam_record, "_generate_run_id", lambda: "fixed-run-id")
    out_root = _out_root(tmp_path)
    out_root.mkdir(parents=True)
    (out_root / "fixed-run-id").mkdir()
    with pytest.raises(redteam_record.RecordCollisionError):
        redteam_record.publish(out_dir=out_root)
    assert list((out_root / "fixed-run-id").iterdir()) == []


def test_target_created_immediately_before_the_claim_is_refused(sandbox, monkeypatch, tmp_path):
    def _race(final):
        final.mkdir()
        (final / "sentinel.txt").write_text("race-injected", encoding="utf-8")

    monkeypatch.setattr(redteam_record, "_pre_claim_hook", _race)
    with pytest.raises(redteam_record.RecordCollisionError):
        redteam_record.publish(out_dir=_out_root(tmp_path))
    out_root = _out_root(tmp_path)
    run_dirs = list(out_root.iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "sentinel.txt").read_text(encoding="utf-8") == "race-injected"
    assert not (run_dirs[0] / redteam_record.JSON_FILENAME).exists()


def test_no_force_overwrite_or_leaf_path_option_exists():
    import inspect

    source = inspect.getsource(redteam_record.main)
    for forbidden in ("--force", "--overwrite"):
        assert forbidden not in source
    for forbidden_call in ("os.replace", "shutil.move", "os.rename"):
        assert forbidden_call not in inspect.getsource(redteam_record)


def test_serialization_failure_creates_no_directory_at_all(sandbox, monkeypatch, tmp_path):
    """Poison the built record with a non-JSON-serializable value (a set), rather than
    monkeypatching the shared `json.dumps` global, which would leak into unrelated code
    during this test."""
    original_build = redteam_record.build_admission_record

    def _poisoned():
        record = original_build()
        record["corpus"]["poison"] = {1, 2, 3}
        return record

    monkeypatch.setattr(redteam_record, "build_admission_record", _poisoned)
    with pytest.raises(TypeError):
        redteam_record.publish(out_dir=_out_root(tmp_path))
    assert _nothing_was_created(tmp_path)


def test_write_failure_with_successful_cleanup_leaves_no_directory(sandbox, monkeypatch, tmp_path):
    """The parent output directory did not exist before this invocation, so a successful
    cleanup must remove BOTH the claimed run-id directory and the parent this invocation
    created. Asserted directly rather than through `_nothing_was_created()`, which would
    also accept an empty leftover parent."""
    from pathlib import Path

    real_write_text = Path.write_text

    def _flaky_write_text(self, *args, **kwargs):
        if self.name == redteam_record.MD_FILENAME:
            raise OSError("simulated disk failure on the second write")
        return real_write_text(self, *args, **kwargs)

    out_root = _out_root(tmp_path)
    assert not out_root.exists()
    monkeypatch.setattr(Path, "write_text", _flaky_write_text)
    with pytest.raises(OSError, match="simulated disk failure"):
        redteam_record.publish(out_dir=out_root)
    assert not out_root.exists()


def test_write_failure_leaves_a_preexisting_parent_and_its_sibling_untouched(
    sandbox, monkeypatch, tmp_path
):
    """A parent that existed before this invocation is never removed, and neither is any
    sibling record inside it — only the directory this invocation claimed is cleaned up."""
    from pathlib import Path

    out_root = _out_root(tmp_path)
    sibling = out_root / "20250101T000000Z-redteam"
    sibling.mkdir(parents=True)
    (sibling / redteam_record.JSON_FILENAME).write_text("{}", encoding="utf-8")
    (sibling / redteam_record.MD_FILENAME).write_text("# earlier record\n", encoding="utf-8")
    before = {
        p.name: p.read_text(encoding="utf-8") for p in sorted(sibling.iterdir())
    }

    real_write_text = Path.write_text

    def _flaky_write_text(self, *args, **kwargs):
        if self.name == redteam_record.MD_FILENAME and self.parent != sibling:
            raise OSError("simulated disk failure on the second write")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _flaky_write_text)
    monkeypatch.setattr(redteam_record, "_generate_run_id", lambda: "new-run-id")
    with pytest.raises(OSError, match="simulated disk failure"):
        redteam_record.publish(out_dir=out_root)

    assert out_root.exists()
    assert not (out_root / "new-run-id").exists()
    assert [p.name for p in sorted(out_root.iterdir())] == [sibling.name]
    after = {p.name: p.read_text(encoding="utf-8") for p in sorted(sibling.iterdir())}
    assert before == after


def test_nonempty_parent_rmdir_refusal_is_benign_and_propagates_the_original_error(
    sandbox, monkeypatch, tmp_path
):
    """Parent removal is a courtesy, not a guarantee. The parent this invocation created can
    stop being empty before cleanup runs — the real-world case being a concurrent
    publication landing a sibling in the same shared parent. That is benign: the claimed
    directory was removed, so there is no incomplete record and nothing to investigate. The
    original write error must propagate unchanged, and RecordPublicationCleanupError — which
    means only that an invalid record remnant is on disk — must NOT be raised.

    Exercised without global monkeypatching of the removal path by using the existing
    `_pre_claim_hook` seam to drop a foreign entry into the parent between its creation and
    the exclusive claim, so the later rmdir fails naturally with ENOTEMPTY."""
    from pathlib import Path

    def _drop_foreign_entry(final):
        (final.parent / "foreign-entry").mkdir()

    real_write_text = Path.write_text

    def _flaky_write_text(self, *args, **kwargs):
        if self.name == redteam_record.MD_FILENAME:
            raise OSError("simulated write failure")
        return real_write_text(self, *args, **kwargs)

    out_root = _out_root(tmp_path)
    assert not out_root.exists()
    monkeypatch.setattr(redteam_record, "_pre_claim_hook", _drop_foreign_entry)
    monkeypatch.setattr(Path, "write_text", _flaky_write_text)
    monkeypatch.setattr(redteam_record, "_generate_run_id", lambda: "concurrent-sibling-run")

    with pytest.raises(OSError, match="simulated write failure") as excinfo:
        redteam_record.publish(out_dir=out_root)
    assert not isinstance(excinfo.value, redteam_record.RecordPublicationCleanupError)

    assert not (out_root / "concurrent-sibling-run").exists()
    assert out_root.exists()
    assert [p.name for p in sorted(out_root.iterdir())] == ["foreign-entry"]


def test_write_failure_with_cleanup_failure_raises_the_chained_cleanup_error(
    sandbox, monkeypatch, tmp_path
):
    from pathlib import Path

    real_write_text = Path.write_text

    def _flaky_write_text(self, *args, **kwargs):
        if self.name == redteam_record.MD_FILENAME:
            raise OSError("simulated write failure")
        return real_write_text(self, *args, **kwargs)

    def _flaky_rmtree(*args, **kwargs):
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(Path, "write_text", _flaky_write_text)
    monkeypatch.setattr(redteam_record.shutil, "rmtree", _flaky_rmtree)
    monkeypatch.setattr(redteam_record, "_generate_run_id", lambda: "cleanup-fail-run")

    with pytest.raises(redteam_record.RecordPublicationCleanupError) as excinfo:
        redteam_record.publish(out_dir=_out_root(tmp_path))

    err = excinfo.value
    assert "cleanup-fail-run" in str(err)
    assert "NOT a valid admission record" in str(err)
    assert "manual investigation" in str(err)
    assert isinstance(err.original_error, OSError)
    assert isinstance(err.cleanup_error, OSError)
    assert "simulated write failure" in str(err.original_error)
    assert "simulated cleanup failure" in str(err.cleanup_error)

    incomplete = _out_root(tmp_path) / "cleanup-fail-run"
    assert incomplete.exists()
    assert (incomplete / redteam_record.JSON_FILENAME).exists()
    assert not (incomplete / redteam_record.MD_FILENAME).exists()
    assert redteam_record._is_valid_record_directory(incomplete) is False


def test_retry_after_cleanup_failure_refuses_as_collision_not_reuse(sandbox, monkeypatch, tmp_path):
    from pathlib import Path

    real_write_text = Path.write_text
    fail_once = {"failed": False}

    def _flaky_write_text(self, *args, **kwargs):
        if self.name == redteam_record.MD_FILENAME and not fail_once["failed"]:
            fail_once["failed"] = True
            raise OSError("simulated write failure")
        return real_write_text(self, *args, **kwargs)

    def _flaky_rmtree(*args, **kwargs):
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(Path, "write_text", _flaky_write_text)
    monkeypatch.setattr(redteam_record.shutil, "rmtree", _flaky_rmtree)
    monkeypatch.setattr(redteam_record, "_generate_run_id", lambda: "retry-run")

    with pytest.raises(redteam_record.RecordPublicationCleanupError):
        redteam_record.publish(out_dir=_out_root(tmp_path))

    incomplete = _out_root(tmp_path) / "retry-run"
    before = (incomplete / redteam_record.JSON_FILENAME).read_text(encoding="utf-8")

    monkeypatch.setattr(redteam_record.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(
        OSError("still failing")
    ))
    with pytest.raises(redteam_record.RecordCollisionError):
        redteam_record.publish(out_dir=_out_root(tmp_path))

    after = (incomplete / redteam_record.JSON_FILENAME).read_text(encoding="utf-8")
    assert before == after
    assert not (incomplete / redteam_record.MD_FILENAME).exists()


def test_a_valid_publication_contains_exactly_the_two_expected_files(sandbox, tmp_path):
    final = redteam_record.publish(out_dir=_out_root(tmp_path))
    assert redteam_record._is_valid_record_directory(final) is True


# ========================================================================================
# Group 9 — pre-write integrity failures (sandboxed + deliberately corrupted manifest)
# ========================================================================================
def test_missing_manifest_raises_and_creates_nothing(tmp_path, monkeypatch):
    corpus = tmp_path / "redteam"
    corpus.mkdir()
    manifest_path = corpus / "manifest.json"  # never created
    monkeypatch.setattr(redteam_corpus, "REDTEAM_DIR", corpus)
    monkeypatch.setattr(redteam_corpus, "MANIFEST_PATH", manifest_path)
    with pytest.raises(redteam_record.RedTeamRecordIntegrityError):
        redteam_record.publish(out_dir=_out_root(tmp_path))
    assert _nothing_was_created(tmp_path)


def test_manifest_schema_mismatch_raises_and_creates_nothing(sandbox, tmp_path):
    _corpus, manifest_path = sandbox
    m = _read_manifest(manifest_path)
    m["schema"] = "some_other_schema"
    _write_manifest(manifest_path, m)
    with pytest.raises(redteam_record.RedTeamRecordIntegrityError):
        redteam_record.publish(out_dir=_out_root(tmp_path))
    assert _nothing_was_created(tmp_path)


def test_nonempty_corrections_raises_and_creates_nothing(sandbox, tmp_path):
    _corpus, manifest_path = sandbox
    m = _read_manifest(manifest_path)
    m["corrections"] = [{"note": "a correction that must never exist"}]
    _write_manifest(manifest_path, m)
    with pytest.raises(redteam_record.RedTeamRecordIntegrityError):
        redteam_record.publish(out_dir=_out_root(tmp_path))
    assert _nothing_was_created(tmp_path)


def test_live_id_set_mismatch_raises_and_creates_nothing(sandbox, tmp_path):
    corpus, _manifest_path = sandbox
    (corpus / "case_rt_02.json").unlink()
    with pytest.raises(redteam_record.RedTeamRecordIntegrityError):
        redteam_record.publish(out_dir=_out_root(tmp_path))
    assert _nothing_was_created(tmp_path)


def test_corpus_hash_mismatch_raises_and_creates_nothing(sandbox, tmp_path):
    corpus, _manifest_path = sandbox
    raw = json.loads((corpus / "case_rt_01.json").read_text(encoding="utf-8"))
    raw["label"]["self_contained_evidence"] = "a different fact than what was frozen"
    (corpus / "case_rt_01.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(redteam_record.RedTeamRecordIntegrityError):
        redteam_record.publish(out_dir=_out_root(tmp_path))
    assert _nothing_was_created(tmp_path)


def test_per_case_hash_mismatch_raises_and_creates_nothing(sandbox, tmp_path):
    _corpus, manifest_path = sandbox
    m = _read_manifest(manifest_path)
    m["per_case"]["rt_01"]["case_hash"] = "sha256:" + "0" * 64
    _write_manifest(manifest_path, m)
    with pytest.raises(redteam_record.RedTeamRecordIntegrityError) as excinfo:
        redteam_record.publish(out_dir=_out_root(tmp_path))
    assert "rt_01" in str(excinfo.value)
    assert _nothing_was_created(tmp_path)


@pytest.mark.parametrize("field", ["category", "appropriate_action", "external_dependency"])
def test_label_field_mismatch_raises_and_creates_nothing(sandbox, tmp_path, field):
    _corpus, manifest_path = sandbox
    m = _read_manifest(manifest_path)
    m["per_case"]["rt_01"][field] = "a-value-that-cannot-be-real"
    _write_manifest(manifest_path, m)
    with pytest.raises(redteam_record.RedTeamRecordIntegrityError) as excinfo:
        redteam_record.publish(out_dir=_out_root(tmp_path))
    assert "rt_01" in str(excinfo.value)
    assert _nothing_was_created(tmp_path)


def test_rationale_mismatch_raises_and_creates_nothing(sandbox, tmp_path):
    _corpus, manifest_path = sandbox
    m = _read_manifest(manifest_path)
    m["per_case"]["rt_01"]["out_of_coverage_rationale"] = "not what the fixture actually says"
    _write_manifest(manifest_path, m)
    with pytest.raises(redteam_record.RedTeamRecordIntegrityError):
        redteam_record.publish(out_dir=_out_root(tmp_path))
    assert _nothing_was_created(tmp_path)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda entry: entry.__setitem__("final_decision", "escalate"),
        lambda entry: entry.__setitem__("findings", [{"origin": "deterministic", "code": "A2"}]),
        lambda entry: entry.__setitem__("admitted", False),
        lambda entry: entry.pop("triage_present", None),
        lambda entry: entry.pop("triage_state", None),
    ],
    ids=["non_approve", "nonempty_findings", "not_admitted", "missing_triage_present", "missing_triage_state"],
)
def test_malformed_historical_observation_raises_and_creates_nothing(sandbox, tmp_path, mutator):
    _corpus, manifest_path = sandbox
    m = _read_manifest(manifest_path)
    entry = next(c for c in m["observed_at_freeze"]["per_case"] if c["case_id"] == "rt_01")
    mutator(entry)
    _write_manifest(manifest_path, m)
    with pytest.raises(redteam_record.RedTeamRecordIntegrityError):
        redteam_record.publish(out_dir=_out_root(tmp_path))
    assert _nothing_was_created(tmp_path)


def test_cross_link_mismatch_raises_and_creates_nothing(sandbox, tmp_path):
    """The bottom observed_at_freeze block stays fully valid (so check 7 passes); only the
    top-level per_case[cid].observed_at_freeze COPY is altered, isolating check 8."""
    _corpus, manifest_path = sandbox
    m = _read_manifest(manifest_path)
    altered = copy.deepcopy(m["per_case"]["rt_01"]["observed_at_freeze"])
    altered["triage_state"] = {"novel_concerns": ["a concern the bottom block does not have"]}
    m["per_case"]["rt_01"]["observed_at_freeze"] = altered
    _write_manifest(manifest_path, m)
    with pytest.raises(redteam_record.RedTeamRecordIntegrityError) as excinfo:
        redteam_record.publish(out_dir=_out_root(tmp_path))
    assert "rt_01" in str(excinfo.value)
    assert "two copies disagree" in str(excinfo.value)
    assert _nothing_was_created(tmp_path)


def test_stub_identity_drift_raises_and_creates_nothing(sandbox, tmp_path, monkeypatch):
    class _FakeStub:
        pass

    monkeypatch.setattr(redteam_record, "PolicyMirrorStub", _FakeStub)
    with pytest.raises(redteam_record.RedTeamRecordIntegrityError) as excinfo:
        redteam_record.publish(out_dir=_out_root(tmp_path))
    assert "offline-stub identity drift" in str(excinfo.value)
    assert "versioned re-triage" in str(excinfo.value).lower() or "VERSIONED RE-TRIAGE" in str(excinfo.value)
    assert _nothing_was_created(tmp_path)


def test_promoted_rule_id_drift_raises_and_creates_nothing(sandbox, tmp_path, monkeypatch):
    drifted = [CandidateRule(rule_id="a-different-rule-v1", template_id="capital_age_ceiling", params={})]
    monkeypatch.setattr(redteam_record, "load_promoted_rules", lambda: drifted)
    with pytest.raises(redteam_record.RedTeamRecordIntegrityError) as excinfo:
        redteam_record.publish(out_dir=_out_root(tmp_path))
    assert "promoted-rule id drift" in str(excinfo.value)
    assert _nothing_was_created(tmp_path)


def test_promoted_rules_hash_drift_raises_and_creates_nothing(sandbox, tmp_path, monkeypatch):
    _corpus, manifest_path = sandbox
    frozen_ids = _read_manifest(manifest_path)["pinned_config"]["promoted_rule_ids"]
    drifted = [
        CandidateRule(
            rule_id=rid,
            template_id="capital_age_ceiling",
            params={"max_age_days": 1, "max_capital": 1},
        )
        for rid in frozen_ids
    ]
    monkeypatch.setattr(redteam_record, "load_promoted_rules", lambda: drifted)
    with pytest.raises(redteam_record.RedTeamRecordIntegrityError) as excinfo:
        redteam_record.publish(out_dir=_out_root(tmp_path))
    assert "promoted-rules hash drift" in str(excinfo.value)
    assert _nothing_was_created(tmp_path)


# ========================================================================================
# Group 10 — separation from every other corpus surface
# ========================================================================================
def test_run_type_and_output_paths_are_disjoint_from_dev_and_p2_artifacts():
    from harness.eval import holdout_report, scientific_report

    assert redteam_record.RUN_TYPE not in (
        getattr(scientific_report, "RUN_TYPE", "deterministic_offline"),
        holdout_report.HOLDOUT_RUN_TYPE,
    )
    assert redteam_record.DEFAULT_OUT_DIR not in (
        "artifacts/p5",  # scientific_report's --out default; no module-level constant exists
        holdout_report.DEFAULT_OUT_DIR,
    )


def test_record_is_never_merged_with_another_corpus_surface():
    record = redteam_record.build_admission_record()
    dumped = json.dumps(record)
    for foreign in ("deterministic_holdout", "deterministic_offline"):
        assert foreign not in dumped

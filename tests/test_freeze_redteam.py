"""P4 — the freeze tool's safeguards.

The real corpus is already frozen and the tool must never write again, so every test here
redirects its paths into ``tmp_path`` and authors a tiny synthetic corpus there — exercising
each safeguard without touching the frozen manifest or the 30 real fixtures.
It covers: the blindness-gate all-or-nothing rule (a caught case aborts, writing nothing),
never-overwrite, the count guard, the initial-freeze-only surface, and that the manifest is
written only after schema validation + the full gate — with complete observed output
(including inert triage state), and hashes that exclude timestamps/git.
"""

from __future__ import annotations

import json
from random import Random
from types import SimpleNamespace

import pytest

from harness.data import freeze_redteam, redteam_corpus
from harness.eval.provenance import hash_corpus
from harness.generate.synthetic import generate_for_code


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def _clean_dossier(case_id: str):
    d = generate_for_code("NONE", 1, Random(1))[0].case.dossier.model_copy(deep=True)
    d.dossier_id = case_id
    return d


def _caught_dossier(case_id: str):
    """A dossier the deterministic layer DOES catch (tax_id mismatch -> A2), so it fails the
    blindness gate — a stand-in for an inadmissible candidate."""
    d = _clean_dossier(case_id)
    d.circular.tax_id = d.circular.tax_id + "9"
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
def sandboxed(tmp_path, monkeypatch):
    corpus = tmp_path / "redteam"
    corpus.mkdir()
    manifest = corpus / "manifest.json"
    monkeypatch.setattr(redteam_corpus, "REDTEAM_DIR", corpus)
    monkeypatch.setattr(redteam_corpus, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(freeze_redteam, "MANIFEST_PATH", manifest)
    return corpus, manifest


def _use_expected_count(monkeypatch, n):
    monkeypatch.setattr(freeze_redteam, "EXPECTED_CASE_COUNT", n)


def _observation(case_ids, *, catch=None, mode="finding"):
    per_case = []
    for cid in case_ids:
        entry = {
            "case_id": cid,
            "appropriate_action": "escalate",
            "final_decision": "approve",
            "findings": [],
            "triage_present": True,
            "triage_state": None,
            "admitted": True,
        }
        if cid == catch:
            if mode == "finding":
                entry["findings"] = [{"origin": "deterministic", "code": "A2"}]
            else:
                entry["final_decision"] = "escalate"
            entry["admitted"] = False
        per_case.append(entry)
    return {
        "note": "synthetic",
        "case_count": len(per_case),
        "admitted_count": sum(c["admitted"] for c in per_case),
        "per_case": per_case,
    }


# --------------------------------------------------------------------------------------
# The blindness-gate admission check (pure)
# --------------------------------------------------------------------------------------
def test_refusal_is_a_nonzero_exit():
    assert issubclass(freeze_redteam.FreezeRefused, SystemExit)


def test_validate_admission_accepts_all_admitted():
    freeze_redteam.validate_admission(_observation(["rt_01", "rt_02"]))


@pytest.mark.parametrize("mode", ["finding", "decision"])
def test_validate_admission_refuses_a_caught_case(mode):
    obs = _observation(["rt_01", "rt_02"], catch="rt_02", mode=mode)
    with pytest.raises(freeze_redteam.FreezeRefused) as exc:
        freeze_redteam.validate_admission(obs)
    assert "rt_02" in str(exc.value)
    assert "rt_01" not in str(exc.value)


# --------------------------------------------------------------------------------------
# Triage serialization must be strictly canonical (no repr() into a frozen artifact)
# --------------------------------------------------------------------------------------
def test_normalize_triage_rejects_an_unsupported_type():
    """An object that is neither None, a Pydantic model, nor a dict must be REJECTED, never
    repr()-ed — repr can embed a memory address, which must not enter a provenance artifact."""
    with pytest.raises(TypeError):
        freeze_redteam._normalize_triage(object())


def test_unsupported_triage_aborts_the_freeze_before_a_manifest_is_written(sandboxed, monkeypatch):
    corpus, manifest = sandboxed
    _use_expected_count(monkeypatch, 2)
    _write_case(corpus, "rt_01", _clean_dossier("rt_01"))
    _write_case(corpus, "rt_02", _clean_dossier("rt_02"))

    # Force the offline path to hand back a triage object of an unsupported type.
    monkeypatch.setattr(
        freeze_redteam,
        "run_agent",
        lambda *a, **k: SimpleNamespace(decision="approve", findings=[], triage=object()),
    )

    with pytest.raises(TypeError):
        freeze_redteam.main([])
    assert not manifest.exists(), "a manifest was written despite an unserializable triage object"
    assert list(corpus.glob("*.tmp")) == []


# --------------------------------------------------------------------------------------
# Safeguards through main()
# --------------------------------------------------------------------------------------
def test_count_guard_refuses_when_not_exactly_expected(sandboxed):
    corpus, manifest = sandboxed  # EXPECTED_CASE_COUNT is the real 30; only 2 written
    _write_case(corpus, "rt_01", _clean_dossier("rt_01"))
    _write_case(corpus, "rt_02", _clean_dossier("rt_02"))
    with pytest.raises(freeze_redteam.FreezeRefused) as exc:
        freeze_redteam.main([])
    assert "expected exactly 30" in str(exc.value)
    assert not manifest.exists()


def test_never_overwrites_an_existing_manifest(sandboxed):
    corpus, manifest = sandboxed
    manifest.write_text(json.dumps({"corpus_version": "1"}), encoding="utf-8")
    with pytest.raises(freeze_redteam.FreezeRefused) as exc:
        freeze_redteam.main([])
    assert "never replaces a manifest" in str(exc.value)


def test_no_force_flag(sandboxed):
    with pytest.raises(SystemExit):
        freeze_redteam.main(["--force"])


def test_no_corrections_flag(sandboxed):
    with pytest.raises(SystemExit):
        freeze_redteam.main(["--corrections-file", "x.json"])


def test_blindness_gate_refuses_and_writes_nothing_when_a_case_is_caught(sandboxed, monkeypatch):
    corpus, manifest = sandboxed
    _use_expected_count(monkeypatch, 2)
    _write_case(corpus, "rt_01", _clean_dossier("rt_01"))
    _write_case(corpus, "rt_02", _caught_dossier("rt_02"), action="escalate")
    with pytest.raises(freeze_redteam.FreezeRefused) as exc:
        freeze_redteam.main([])
    assert "rt_02" in str(exc.value)
    assert not manifest.exists(), "a manifest was written despite a caught case"
    assert list(corpus.glob("*.tmp")) == []


# --------------------------------------------------------------------------------------
# Happy path into the sandbox (admissible cases only)
# --------------------------------------------------------------------------------------
@pytest.fixture
def frozen(sandboxed, monkeypatch):
    corpus, manifest = sandboxed
    _use_expected_count(monkeypatch, 2)
    _write_case(corpus, "rt_01", _clean_dossier("rt_01"), ext_dep="pinned_library_behavior")
    _write_case(corpus, "rt_02", _clean_dossier("rt_02"))
    freeze_redteam.main([])
    return corpus, manifest, json.loads(manifest.read_text(encoding="utf-8"))


def test_successful_freeze_writes_a_verifiable_manifest(frozen):
    corpus, manifest, m = frozen
    assert manifest.exists()
    assert m["schema"] == "redteam_out_of_coverage_manifest"
    assert m["case_count"] == 2
    assert m["corpus_hash"] == hash_corpus(redteam_corpus.redteam_payloads())
    assert m["admission"]["result"] == "2/2"
    assert "by_construction" in m["admission"]["interpretation"]
    assert list(corpus.glob("*.tmp")) == []


def test_manifest_records_git_head_branch_and_dirty(frozen):
    _c, _m, m = frozen
    assert set(m["git"]) == {"head_commit_sha", "branch", "worktree_dirty"}


def test_dependencies_are_derived_from_labels(frozen):
    _c, _m, m = frozen
    assert m["dependencies"]["pinned_library_behavior"] == ["rt_01"]
    assert m["dependencies"]["pinned_rule_parameter"] == []


def test_observed_at_freeze_records_complete_output_not_findings_alone(frozen):
    _c, _m, m = frozen
    for entry in m["observed_at_freeze"]["per_case"]:
        assert set(entry) >= {
            "case_id", "final_decision", "findings", "triage_present", "triage_state", "admitted"
        }
        assert entry["final_decision"] == "approve"
        assert entry["findings"] == []
        assert entry["triage_present"] is True  # present-but-inert, recorded as state
    # per-case hash present in the pinned block
    assert all("case_hash" in v for v in m["per_case"].values())


def test_corpus_hash_excludes_timestamp_and_git(frozen):
    """The hash is over {case_id, category, dossier, label} only; the manifest's timestamp
    and git block live outside it, so recomputing from payloads matches exactly."""
    _c, _m, m = frozen
    assert m["corpus_hash"] == hash_corpus(redteam_corpus.redteam_payloads())
    assert "frozen_at_utc" in m["provenance"]  # present, but NOT an input to corpus_hash


def test_second_freeze_into_the_same_sandbox_is_refused(frozen):
    _c, _m, _m2 = frozen
    with pytest.raises(freeze_redteam.FreezeRefused):
        freeze_redteam.main([])

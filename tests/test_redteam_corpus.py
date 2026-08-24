"""P4 — eval-only models + the strict red-team loader.

Fixture-free: the 30 frozen cases are not authored yet, so every test here builds its own
synthetic ``rt_NN`` fixtures in a temp directory and points the loader at them. This
exercises the parsing/validation/integrity contract without depending on (or authoring) the
real corpus.
"""

from __future__ import annotations

import json
from random import Random

import pytest
from pydantic import BaseModel, ValidationError

from harness.contracts.documents import Dossier
from harness.contracts.redteam import RedTeamCase, RedTeamLabel
from harness.data import redteam_corpus
from harness.generate.synthetic import generate_for_code


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def _clean_dossier(case_id: str) -> Dossier:
    d = generate_for_code("NONE", 1, Random(1))[0].case.dossier.model_copy(deep=True)
    d.dossier_id = case_id
    return d


def _label(**overrides):
    base = {
        "appropriate_action": "request_more_info",
        "label_basis": "threat_model_judgment",
        "label_review": "human_approved",
        "authoring_assistance": "llm_assisted",
        "self_contained_evidence": "synthetic self-contained fact",
        "out_of_coverage_rationale": "outside every deterministic check's scope",
        "external_dependency": "none",
    }
    base.update(overrides)
    return base


def _write_case(dirpath, case_id, category, dossier=None, label=None):
    payload = {
        "case_id": case_id,
        "category": category,
        "dossier": (dossier or _clean_dossier(case_id)).model_dump(mode="json"),
        "label": label or _label(),
    }
    (dirpath / f"case_{case_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def corpus_dir(tmp_path, monkeypatch):
    d = tmp_path / "redteam"
    d.mkdir()
    monkeypatch.setattr(redteam_corpus, "REDTEAM_DIR", d)
    monkeypatch.setattr(redteam_corpus, "MANIFEST_PATH", d / "manifest.json")
    return d


# --------------------------------------------------------------------------------------
# RedTeamLabel / RedTeamCase model contract
# --------------------------------------------------------------------------------------
def test_valid_label_constructs():
    lbl = RedTeamLabel(**_label(appropriate_action="escalate", external_dependency="pinned_rule_parameter"))
    assert lbl.appropriate_action == "escalate"
    assert lbl.label_basis == "threat_model_judgment"


def test_label_rejects_approve_action():
    with pytest.raises(ValidationError):
        RedTeamLabel(**_label(appropriate_action="approve"))


def test_label_rejects_unknown_external_dependency():
    with pytest.raises(ValidationError):
        RedTeamLabel(**_label(external_dependency="external_lookup"))


@pytest.mark.parametrize("field", ["self_contained_evidence", "out_of_coverage_rationale"])
def test_label_rejects_empty_required_prose(field):
    with pytest.raises(ValidationError):
        RedTeamLabel(**_label(**{field: "   "}))


def test_label_forbids_extra_fields():
    with pytest.raises(ValidationError):
        RedTeamLabel(**_label(surprise="x"))


def test_case_rejects_bad_category():
    with pytest.raises(ValidationError):
        RedTeamCase(
            case_id="rt_01", category="R7", dossier=_clean_dossier("rt_01"), label=RedTeamLabel(**_label())
        )


# --------------------------------------------------------------------------------------
# expected_category — the fixed rt_NN -> category map
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "case_id,category",
    [
        ("rt_01", "R1"), ("rt_05", "R1"), ("rt_06", "R2"), ("rt_10", "R2"),
        ("rt_11", "R3"), ("rt_15", "R3"), ("rt_16", "R4"), ("rt_20", "R4"),
        ("rt_21", "R5"), ("rt_25", "R5"), ("rt_26", "R6"), ("rt_30", "R6"),
    ],
)
def test_expected_category_map(case_id, category):
    assert redteam_corpus.expected_category(case_id) == category


@pytest.mark.parametrize("bad", ["rt_00", "rt_31", "hold_01", "rt_ab", "rt_"])
def test_expected_category_rejects_out_of_range_or_malformed(bad):
    with pytest.raises(ValueError):
        redteam_corpus.expected_category(bad)


# --------------------------------------------------------------------------------------
# Loader: strictness, agent/truth separation, integrity
# --------------------------------------------------------------------------------------
def test_loader_refuses_non_redteam_ids(corpus_dir):
    with pytest.raises(ValueError):
        redteam_corpus.load_redteam_dossier("case_01")
    with pytest.raises(FileNotFoundError):
        redteam_corpus.load_redteam_dossier("rt_99")


def test_agent_path_returns_dossier_without_the_label(corpus_dir):
    _write_case(corpus_dir, "rt_01", "R1")
    dossier = redteam_corpus.load_redteam_dossier("rt_01")
    assert isinstance(dossier, Dossier)
    assert not isinstance(dossier, RedTeamCase)
    assert not hasattr(dossier, "label")
    assert "label" not in Dossier.model_fields


def test_eval_path_is_the_only_route_to_the_label(corpus_dir):
    _write_case(corpus_dir, "rt_06", "R2", label=_label(appropriate_action="escalate"))
    case = redteam_corpus.load_redteam_case("rt_06")
    assert isinstance(case, RedTeamCase)
    assert case.category == "R2"
    assert case.label.appropriate_action == "escalate"


def test_integrity_rejects_dossier_id_mismatch(corpus_dir):
    d = _clean_dossier("rt_02_WRONG")
    _write_case(corpus_dir, "rt_02", "R1", dossier=d)  # dossier_id != case_id
    with pytest.raises(ValueError):
        redteam_corpus.load_redteam_case("rt_02")


def test_integrity_rejects_case_id_field_mismatch(corpus_dir):
    payload = {
        "case_id": "rt_03_typo",
        "category": "R1",
        "dossier": _clean_dossier("rt_03").model_dump(mode="json"),
        "label": _label(),
    }
    (corpus_dir / "case_rt_03.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        redteam_corpus.load_redteam_case("rt_03")


def test_integrity_rejects_wrong_category(corpus_dir):
    _write_case(corpus_dir, "rt_07", "R1")  # rt_07 must be R2
    with pytest.raises(ValueError):
        redteam_corpus.load_redteam_case("rt_07")


def test_list_ids_and_payloads_roundtrip(corpus_dir):
    _write_case(corpus_dir, "rt_01", "R1")
    _write_case(corpus_dir, "rt_06", "R2")
    assert redteam_corpus.list_redteam_ids() == ["rt_01", "rt_06"]
    payloads = dict(redteam_corpus.redteam_payloads())
    assert set(payloads["rt_01"]) == {"case_id", "category", "dossier", "label"}
    assert payloads["rt_06"]["category"] == "R2"


def test_dossier_and_redteamcase_are_disjoint_types():
    assert issubclass(RedTeamCase, BaseModel)
    assert not issubclass(Dossier, RedTeamCase)
    assert "decision_truth" not in Dossier.model_fields

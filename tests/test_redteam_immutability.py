"""P4 — immutability: the red-team corpus and its frozen manifest are hash-pinned.

A red-team corpus that can be quietly edited is not a red-team corpus. These tests make
"frozen" a mechanically enforced property rather than a documented intention, mirroring the
intent of P2's ``test_holdout_immutability.py``.

Two different surfaces, deliberately kept apart
-----------------------------------------------
* **This module — the FROZEN HISTORICAL RECORD.** It recomputes what is derivable from
  fixture content today (canonical corpus hash, all 30 canonical case hashes, the case-id
  set, and each case's category / action / dependency / rationale) and compares it against
  the manifest written by the one-and-only freeze. It additionally validates that the frozen
  record is structurally complete and that its framing has not been rewritten. It is
  **read-only**: it never imports or invokes the freeze writer, never calls ``run_agent``,
  never generates a fixture or manifest, and never writes anything.
* **``test_redteam_fixtures.py`` — CURRENT-CONFIG BEHAVIOR.** That module re-observes every
  case under today's pinned offline stub and promoted rules
  (``test_case_is_blind_admissible_offline``). Re-running the agent here would duplicate it
  and, worse, blur the very distinction this module exists to keep sharp: a failure would be
  ambiguous between "a fixture moved" and "behavior moved".

The frozen ``observed_at_freeze`` block is therefore treated as a **historical observation**,
never re-derived and never re-interpreted. ``triage_present`` / ``triage_state`` are asserted
to be **present as recorded fields** only. A present-but-inert triage object is recorded
state; nothing here infers, asserts, or claims semantic detection or non-detection by the
triage layer, at freeze time or now.

Pinned-configuration identity is asserted STRICTLY
--------------------------------------------------
The current promoted-rule ids and the canonically recomputed promoted-rules hash must still
equal the frozen ``pinned_config``, and the frozen offline-stub identity must still be the
expected ``PolicyMirrorStub``. A future legitimate rule promotion will therefore fail this
P4(a) test. **That is intentional.** P4's admission record is meaningful only under the
pinned configuration; without this assertion a promotion would silently leave a frozen
``30/30`` standing as though it described the new configuration. The remedy is a separately
designed **versioned re-triage** observation/artifact against the new configuration.

**Cardinal rule, restated where it is enforced.** A hash, inventory or configuration mismatch
is a RESULT, not a chore. It is never resolved by regenerating the manifest, editing a
fixture, editing a label, or adjusting a canonical hash. P4 is initial-freeze-only: the
freeze tool has no ``--force`` and no correction path, so there is deliberately no "just
re-freeze" instruction to give here.
"""

from __future__ import annotations

import json

import pytest

from harness.data.redteam_corpus import (
    EXPECTED_CASE_COUNT,
    MANIFEST_PATH,
    case_payload,
    expected_category,
    list_redteam_ids,
    load_manifest,
    load_redteam_case,
    redteam_payloads,
)
from harness.eval.provenance import hash_corpus, hash_promoted_rules, sha256_of
from harness.llm.stub import PolicyMirrorStub
from harness.rules.store import load_promoted_rules

ALL_IDS = [f"rt_{i:02d}" for i in range(1, EXPECTED_CASE_COUNT + 1)]

EXPECTED_STUB_IDENTITY = "harness.llm.stub.PolicyMirrorStub"

# Pinned inventories, restated here as readable expectations. Strictly redundant with the
# canonical hashes (which cover every label field), but they turn an opaque hash failure into
# a legible one — "the escalate set changed" rather than "something, somewhere, moved".
EXPECTED_ESCALATE_IDS = {"rt_06", "rt_08", "rt_09", "rt_10", "rt_11", "rt_18", "rt_20"}
EXPECTED_RULE_PARAMETER_IDS = {"rt_21", "rt_22", "rt_24", "rt_25"}
EXPECTED_LIBRARY_BEHAVIOR_IDS = {"rt_26", "rt_27"}

_CARDINAL = (
    "This is a RESULT, not a chore. NEVER resolve it by regenerating the manifest, editing a "
    "fixture or its label, or adjusting a canonical hash — that would convert the red-team "
    "corpus into a mirror of the code it exists to probe. P4 is initial-freeze-only (no "
    "--force, no correction path). If a case genuinely starts behaving differently under a "
    "changed configuration or library version, that is a NEW, VERSIONED RE-TRIAGE observation "
    "recorded against the new configuration — never a retroactive edit to this frozen record."
)

# Keys that would turn the by-construction admission record into a published empirical rate.
_EMPIRICAL_RATE_KEYS = ("k", "n", "point_estimate", "wilson_ci_95", "false_approval_rate")


def _manifest() -> dict:
    return load_manifest()


# --------------------------------------------------------------------------------------
# A. Manifest presence and P4 schema identity
# --------------------------------------------------------------------------------------
def test_redteam_manifest_exists():
    assert MANIFEST_PATH.exists(), (
        f"redteam manifest missing at {MANIFEST_PATH}; it is written once by "
        "`python -m harness.data.freeze_redteam`"
    )


def test_manifest_is_valid_json_and_declares_the_p4_schema():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["schema"] == "redteam_out_of_coverage_manifest"
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["corpus_name"] == "redteam_out_of_coverage"
    assert manifest["scope"] == "out_of_coverage"
    assert manifest["corpus_version"]
    assert manifest["case_count"] == EXPECTED_CASE_COUNT


def test_manifest_uses_p4s_own_schema_not_p2s_holdout_schema():
    """P4 deliberately does NOT reuse P2's holdout manifest schema: different label contract,
    different admission criterion, a dependency taxonomy, different provenance requirements.
    This fails if the two are later 'harmonized' into one shape."""
    manifest = _manifest()
    assert manifest["schema"] != "deterministic_holdout"
    assert manifest["corpus_name"] != "deterministic_holdout"
    assert manifest["scope"] != "in_coverage"


def test_manifest_is_initial_freeze_only_with_no_corrections():
    """P4 has no correction or migration path; ``corrections`` is reserved for a future
    versioned-corpus migration that PRESERVES this manifest, and must still be empty."""
    corrections = _manifest()["corrections"]
    assert isinstance(corrections, list)
    assert corrections == [], (
        f"a corrections entry appeared in an initial-freeze-only manifest. {_CARDINAL}"
    )


# --------------------------------------------------------------------------------------
# B. The case-id set: live fixtures and manifest entries agree, both directions
# --------------------------------------------------------------------------------------
def test_live_fixture_ids_are_exactly_rt_01_through_rt_30():
    assert list_redteam_ids() == ALL_IDS


def test_manifest_per_case_ids_match_the_fixtures_on_disk():
    """Catches both directions: a fixture added without a re-freeze, and a fixture deleted
    while its manifest entry lingers."""
    assert sorted(_manifest()["per_case"]) == list_redteam_ids(), _CARDINAL


# --------------------------------------------------------------------------------------
# C+D. Canonical hashes: corpus-level, then per case
# --------------------------------------------------------------------------------------
def test_corpus_hash_matches_the_frozen_manifest():
    assert hash_corpus(redteam_payloads()) == _manifest()["corpus_hash"], (
        f"the red-team corpus content changed since it was frozen. {_CARDINAL}"
    )


@pytest.mark.parametrize("case_id", ALL_IDS)
def test_case_hash_matches_the_frozen_manifest(case_id: str):
    """Per-case pins localize a drift to the exact fixture that moved, instead of only
    telling you that something, somewhere, changed."""
    per_case = _manifest()["per_case"]
    assert case_id in per_case, f"{case_id} is not in the frozen manifest. {_CARDINAL}"
    assert sha256_of(case_payload(case_id)) == per_case[case_id]["case_hash"], (
        f"{case_id} changed since it was frozen. {_CARDINAL}"
    )


# --------------------------------------------------------------------------------------
# E. Pinned label facts agree with the live fixtures
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("case_id", ALL_IDS)
def test_fixture_label_fields_agree_with_the_pinned_manifest_entry(case_id: str):
    """Redundant with the case hash, deliberately: a hash mismatch says only 'something in
    this case moved', while these say which contract field moved."""
    entry = _manifest()["per_case"][case_id]
    case = load_redteam_case(case_id)

    assert case.category == entry["category"], _CARDINAL
    assert case.category == expected_category(case_id), (
        f"{case_id} no longer matches the fixed rt_NN->category map. {_CARDINAL}"
    )
    assert case.label.appropriate_action == entry["appropriate_action"], _CARDINAL
    assert case.label.external_dependency == entry["external_dependency"], _CARDINAL
    assert case.label.out_of_coverage_rationale == entry["out_of_coverage_rationale"], _CARDINAL
    assert case.label.out_of_coverage_rationale.strip(), f"{case_id} rationale is empty"


def test_observed_action_distribution_is_still_23_rmi_7_escalate_0_approve():
    """The distribution is an OBSERVED result of authoring, never a quota — but it is frozen,
    so a change to it is a corpus change."""
    actions = [load_redteam_case(cid).label.appropriate_action for cid in ALL_IDS]
    assert actions.count("request_more_info") == 23, _CARDINAL
    assert actions.count("escalate") == 7, _CARDINAL
    assert actions.count("approve") == 0, _CARDINAL


def test_escalate_case_set_is_unchanged():
    escalate = {
        cid for cid in ALL_IDS
        if load_redteam_case(cid).label.appropriate_action == "escalate"
    }
    assert escalate == EXPECTED_ESCALATE_IDS, _CARDINAL


def test_dependency_inventory_matches_the_frozen_manifest():
    """The `pinned_rule_parameter` / `pinned_library_behavior` sets are exactly the cases
    whose admissibility depends on the pinned configuration rather than on a
    config-independent property — the population a future re-triage must revisit first."""
    dependencies = _manifest()["dependencies"]
    live: dict[str, set[str]] = {}
    for cid in ALL_IDS:
        live.setdefault(load_redteam_case(cid).label.external_dependency, set()).add(cid)

    assert live.get("pinned_rule_parameter", set()) == EXPECTED_RULE_PARAMETER_IDS, _CARDINAL
    assert live.get("pinned_library_behavior", set()) == EXPECTED_LIBRARY_BEHAVIOR_IDS, _CARDINAL
    assert set(dependencies["pinned_rule_parameter"]) == EXPECTED_RULE_PARAMETER_IDS, _CARDINAL
    assert set(dependencies["pinned_library_behavior"]) == EXPECTED_LIBRARY_BEHAVIOR_IDS, _CARDINAL
    assert live.get("none", set()) == (
        set(ALL_IDS) - EXPECTED_RULE_PARAMETER_IDS - EXPECTED_LIBRARY_BEHAVIOR_IDS
    ), _CARDINAL


# --------------------------------------------------------------------------------------
# F. The frozen admission record is structurally complete (historical, never re-derived)
# --------------------------------------------------------------------------------------
def test_frozen_admission_record_covers_all_thirty_cases():
    observation = _manifest()["observed_at_freeze"]
    assert observation["case_count"] == EXPECTED_CASE_COUNT
    assert observation["admitted_count"] == EXPECTED_CASE_COUNT
    assert len(observation["per_case"]) == EXPECTED_CASE_COUNT
    assert sorted(c["case_id"] for c in observation["per_case"]) == ALL_IDS


@pytest.mark.parametrize("case_id", ALL_IDS)
def test_frozen_per_case_observation_is_complete_and_admitted(case_id: str):
    """What the freeze RECORDED, re-read — not re-observed. ``triage_present`` and
    ``triage_state`` are required recorded fields; their contents are historical state and
    are NOT read as evidence that the triage layer did or did not detect anything."""
    observation = _manifest()["observed_at_freeze"]
    entry = next(c for c in observation["per_case"] if c["case_id"] == case_id)

    assert entry["final_decision"] == "approve", _CARDINAL
    assert entry["findings"] == [], _CARDINAL
    assert entry["admitted"] is True, _CARDINAL
    assert "triage_present" in entry, "the frozen record must carry triage_present"
    assert "triage_state" in entry, "the frozen record must carry triage_state"
    # Cross-check against the per-case manifest entry's copy of the same observation.
    assert _manifest()["per_case"][case_id]["observed_at_freeze"] == entry, _CARDINAL


def test_admission_result_is_thirty_of_thirty_under_the_blindness_criterion():
    admission = _manifest()["admission"]
    assert admission["result"] == f"{EXPECTED_CASE_COUNT}/{EXPECTED_CASE_COUNT}"
    criterion = admission["criterion"].lower()
    assert "approve" in criterion
    assert "zero findings" in criterion
    assert "any origin" in criterion


def test_admission_is_framed_as_by_construction_and_never_as_an_empirical_rate():
    """Guards the FRAMING itself, the P4 analogue of P2's empirical-vs-structural test. The
    30/30 admission is a by-construction corpus-admission property: every admitted case is
    REQUIRED to approve with no findings. If someone later edits the manifest builder to
    publish it as a false-approval rate — or attaches a Wilson interval to it — this fails."""
    admission = _manifest()["admission"]
    interpretation = admission["interpretation"].lower()
    assert "by_construction" in interpretation
    assert "not an empirical false-approval rate" in interpretation
    assert "no confidence interval" in interpretation
    for key in _EMPIRICAL_RATE_KEYS:
        assert key not in admission, (
            f"admission record carries {key!r}: the 30/30 admission is by construction and "
            "must never be published as a rate with counts or a confidence interval"
        )
        assert key not in _manifest()["observed_at_freeze"], (
            f"observed_at_freeze carries {key!r}: the frozen observation is a record of "
            "admission, not a published empirical rate"
        )


def test_scope_note_keeps_p4_outside_the_in_coverage_far_invariant():
    scope_note = _manifest()["scope_note"].lower()
    assert "out_of_coverage" in _manifest()["scope"]
    assert "not reported as an empirical" in scope_note
    assert "in-coverage" in scope_note


# --------------------------------------------------------------------------------------
# G. Pinned-configuration identity — STRICT (see the module docstring)
# --------------------------------------------------------------------------------------
_CONFIG_DRIFT = (
    "The pinned configuration has changed since the freeze. The frozen 30/30 admission "
    "describes the OLD configuration and says nothing about this one, so it must not be "
    "read as though it did. The remedy is a separately designed VERSIONED RE-TRIAGE "
    "observation/artifact against the new configuration — never an edit to fixtures, labels, "
    "canonical hashes, or this historical manifest."
)


def test_frozen_offline_stub_identity_is_the_expected_policy_mirror_stub():
    frozen = _manifest()["pinned_config"]["offline_stub"]
    assert frozen == EXPECTED_STUB_IDENTITY, _CONFIG_DRIFT
    live = f"{PolicyMirrorStub.__module__}.{PolicyMirrorStub.__qualname__}"
    assert live == EXPECTED_STUB_IDENTITY, (
        f"the offline stub moved to {live!r}. {_CONFIG_DRIFT}"
    )


def test_currently_promoted_rule_ids_match_the_frozen_pin():
    frozen_ids = _manifest()["pinned_config"]["promoted_rule_ids"]
    assert frozen_ids == ["capital-age-v1"], _CONFIG_DRIFT
    live_ids = [r.rule_id for r in load_promoted_rules()]
    assert sorted(live_ids) == sorted(frozen_ids), (
        f"promoted rule ids are now {sorted(live_ids)}, frozen as {sorted(frozen_ids)}. "
        f"{_CONFIG_DRIFT}"
    )


def test_currently_promoted_rules_hash_matches_the_frozen_pin():
    """Recomputed with the SAME canonical P5 primitive the freeze used
    (``hash_promoted_rules`` over ``CandidateRule.model_dump()`` dicts), so a parameter change
    to an existing rule is caught as well as an added or removed rule.

    A future legitimate promotion will fail this test. That is the intended forcing function:
    it makes configuration drift visible instead of letting the historical admission record
    stand in for a configuration it never described."""
    frozen_hash = _manifest()["pinned_config"]["promoted_rules_hash"]
    assert frozen_hash.startswith("sha256:")
    live_hash = hash_promoted_rules([r.model_dump() for r in load_promoted_rules()])
    assert live_hash == frozen_hash, (
        f"promoted-rules hash is now {live_hash}, frozen as {frozen_hash}. {_CONFIG_DRIFT}"
    )

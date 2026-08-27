"""P4(b) — the ``redteam_admission_record``: an offline admission-and-provenance RECORD.

This is deliberately NOT a scientific report, a result, a score, or a performance artifact.
It never calls the shared metrics-report aggregator used by the development and P2 holdout
surfaces, never constructs a metrics-report record object, and never runs the agent — not
even as an unpublished guard. The P4 red-team corpus carries
human-reviewed threat-model JUDGMENTS (``RedTeamLabel``), not a ``DecisionTruth`` prediction
of harness output; treating a label as an oracle and diffing it against a fresh run would
manufacture an empirical false-approval rate out of a by-construction admission property,
which is the one thing P4 must never publish. Current-config blindness is re-observed
exclusively by ``tests/test_redteam_fixtures.py``; this module only ever reads and
republishes the manifest's HISTORICAL ``observed_at_freeze`` record, unchanged.

What this module does
----------------------
Independently re-validates the COMPLETE frozen-manifest contract this record republishes
(schema identity, the fixed case-id set, the canonical corpus hash, all 30 canonical
per-case hashes, every live label field the record copies, the structural completeness and
internal cross-linking of the historical admission observation, and the CURRENT
offline-stub / promoted-rule identity against the frozen pin) — then builds a small JSON +
Markdown record describing the frozen corpus's identity and its by-construction
``30/30`` admission, and publishes both as one atomic unit under a generated,
never-reused ``run_id`` directory.

Configuration drift is a hard failure, not a labelled artifact
----------------------------------------------------------------
If the live offline-stub identity, the live promoted-rule id set, or the recomputed
promoted-rules hash no longer matches the frozen pin, this module writes NOTHING — no
``stale_config`` artifact, no partial record, no output directory. The frozen ``30/30``
admission describes the pinned configuration only; publishing it beside a ``run`` block for
a DIFFERENT configuration would misrepresent one for the other. The remedy is a separately
designed VERSIONED RE-TRIAGE observation against the new configuration, never a re-freeze,
and never an edit to a fixture, label, canonical hash, or the frozen manifest.

Publication lifecycle
----------------------
Every publication lands under its own generated ``run_id`` directory
(``artifacts/p4_redteam/<run_id>/``) and is NEVER overwritten. Repeated valid publications
coexist as sibling directories over an unchanged ``corpus_hash`` — they differ only in their
``run`` provenance block (timestamp, publication-code git SHA, package versions), never in
corpus identity. There is no mutable "latest" pointer.

The directory is claimed with an exclusive ``os.mkdir`` — atomic and exclusive on both POSIX
(``EEXIST``) and Windows (``ERROR_ALREADY_EXISTS``), surfacing as ``FileExistsError`` on
either. There is no check-then-act gap: the claim IS the check. A directory is a valid
record if and only if it contains EXACTLY two regular files, ``redteam_admission_record.json``
and ``redteam_admission_record.md``, and no other entries — no extra files, no symlinked or
directory-typed expected path. Markdown is written last, as the completion marker.

If a write fails after the directory is claimed, cleanup is attempted; if cleanup itself
fails, a distinct, chained ``RecordPublicationCleanupError`` is raised naming the incomplete
directory and requiring manual investigation — this module never claims guaranteed cleanup
under filesystem failure, and a retry against ANY pre-existing directory at the same
``run_id`` (valid, empty, or partial) always refuses as a collision. There is no code path
that inspects, repairs, or reuses an existing directory.

The cleanup GUARANTEE covers the claimed ``run_id`` directory only. As a courtesy on top of
it, if THIS invocation created the parent output directory, a successful cleanup is followed
by a single best-effort, non-recursive ``rmdir`` of that now-empty parent, so an ordinary
failed publication leaves no trace in the listing it found. That parent removal is never
promised: a parent that existed before this invocation is NEVER removed, the parent is never
deleted recursively, and if the ``rmdir`` fails for any reason — most commonly because a
concurrent publication has since landed a sibling in the same parent, which is a benign
outcome over shared state — the failure is swallowed and the original publication exception
propagates unchanged. ``RecordPublicationCleanupError`` means one thing only: the claimed
record directory itself could not be removed and an incomplete record remnant is on disk.

Two Git SHAs, kept explicitly separate
----------------------------------------
``run.git.commit_sha`` is PUBLICATION provenance: the code state that generated this record.
``corpus.frozen_git.head_commit_sha`` is the dirty BOOTSTRAP context the freeze ran against.
Neither is the corpus's reproducibility anchor — ``corpus.corpus_hash`` over
contract-normalized content is the sole anchor. All three notes are carried inside the
record itself (``provenance_notes``), each rendered immediately beside the field it
qualifies, so no SHA can be read without its scope.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.data import redteam_corpus
from harness.eval import provenance
from harness.llm.stub import PolicyMirrorStub
from harness.rules.store import load_promoted_rules

RECORD_SCHEMA = "redteam_admission_record"
# 2.0.0: BREAKING. `corpus.authoring` no longer emits "hand_authored"; it emits
# "llm_assisted_human_reviewed", and a separate `corpus.derivation` key was added. Removing
# a value from a field's domain breaks any consumer that branches on the literal, so this is
# a major bump even though no field was removed or renamed. Nothing in this repository
# parses either value. See ERRATA.md.
RECORD_SCHEMA_VERSION = "2.0.0"
RUN_TYPE = "redteam_out_of_coverage"

DEFAULT_OUT_DIR = "artifacts/p4_redteam"
JSON_FILENAME = "redteam_admission_record.json"
MD_FILENAME = "redteam_admission_record.md"

_EXPECTED_MANIFEST_SCHEMA = "redteam_out_of_coverage_manifest"
_EXPECTED_MANIFEST_SCHEMA_VERSION = "1.0.0"
EXPECTED_STUB_IDENTITY = "harness.llm.stub.PolicyMirrorStub"


# --------------------------------------------------------------------------------------
# Exception hierarchy
# --------------------------------------------------------------------------------------
class RedTeamRecordError(Exception):
    """Base for every P4(b) record-lifecycle error."""


class RedTeamRecordIntegrityError(RedTeamRecordError):
    """A pre-write check against the frozen manifest, the live corpus, or the live
    configuration failed. Raised BEFORE any output directory is created."""


class RecordCollisionError(RedTeamRecordError):
    """The generated run-id directory already exists. Publication never overwrites or
    reuses an existing directory, valid or not — the pre-existing directory is left
    byte-for-byte intact."""


class RecordPublicationCleanupError(RedTeamRecordError):
    """A post-claim write failed AND best-effort cleanup of the claimed directory also
    failed. The incomplete directory is left on disk and requires manual investigation
    before any retry of the same run_id; it is never treated as a valid record."""

    def __init__(
        self, message: str, *, original_error: BaseException, cleanup_error: BaseException
    ) -> None:
        super().__init__(message)
        self.original_error = original_error
        self.cleanup_error = cleanup_error


# --------------------------------------------------------------------------------------
# Integrity guard — the eleven pre-write checks, in order
# --------------------------------------------------------------------------------------
_CARDINAL = (
    "This is a RESULT to investigate, not a chore to silence. Never resolve it by "
    "regenerating the manifest, editing a fixture or its label, or adjusting a canonical "
    "hash — that would convert the red-team corpus into a mirror of the code it exists to "
    "probe."
)

_CONFIG_DRIFT = (
    "The pinned configuration has changed since the freeze. The frozen 30/30 admission "
    "describes the OLD configuration and says nothing about this one, so no record can be "
    "published as though it did. The remedy is a separately designed VERSIONED RE-TRIAGE "
    "observation/artifact against the new configuration — never a re-freeze, never an edit "
    "to fixtures, labels, canonical hashes, or the frozen manifest."
)


def validate_frozen_contract() -> dict[str, Any]:
    """Re-validate the COMPLETE frozen-manifest contract this record will republish,
    against the live corpus and the live configuration. Raises
    ``RedTeamRecordIntegrityError`` on the first failure. Nothing is created, opened for
    writing, or mutated here — every check is a comparison, never an observation of
    current agent behavior. Returns the validated manifest dict."""
    try:
        manifest = redteam_corpus.load_manifest()
    except FileNotFoundError as exc:
        raise RedTeamRecordIntegrityError(
            f"no frozen redteam manifest found: {exc}. {_CARDINAL}"
        ) from exc

    # 1. Manifest schema identity
    if manifest.get("schema") != _EXPECTED_MANIFEST_SCHEMA:
        raise RedTeamRecordIntegrityError(
            f"manifest schema is {manifest.get('schema')!r}, expected "
            f"{_EXPECTED_MANIFEST_SCHEMA!r}. {_CARDINAL}"
        )
    if manifest.get("schema_version") != _EXPECTED_MANIFEST_SCHEMA_VERSION:
        raise RedTeamRecordIntegrityError(
            f"manifest schema_version is {manifest.get('schema_version')!r}, expected "
            f"{_EXPECTED_MANIFEST_SCHEMA_VERSION!r}. {_CARDINAL}"
        )

    # 2. Corpus identity / initial-freeze-only contract
    expected_count = redteam_corpus.EXPECTED_CASE_COUNT
    if manifest.get("scope") != "out_of_coverage":
        raise RedTeamRecordIntegrityError(
            f"manifest scope is {manifest.get('scope')!r}, expected 'out_of_coverage'. "
            f"{_CARDINAL}"
        )
    if not manifest.get("corpus_name") or not manifest.get("corpus_version"):
        raise RedTeamRecordIntegrityError(
            f"manifest is missing corpus_name or corpus_version. {_CARDINAL}"
        )
    if manifest.get("case_count") != expected_count:
        raise RedTeamRecordIntegrityError(
            f"manifest case_count is {manifest.get('case_count')!r}, expected "
            f"{expected_count}. {_CARDINAL}"
        )
    if manifest.get("corrections") != []:
        raise RedTeamRecordIntegrityError(
            "manifest 'corrections' is non-empty; P4 is initial-freeze-only and never "
            f"populates this. {_CARDINAL}"
        )

    # 3. Live id set == manifest per_case keys == the fixed contiguous rt_NN set
    expected_ids = [f"rt_{i:02d}" for i in range(1, expected_count + 1)]
    live_ids = redteam_corpus.list_redteam_ids()
    manifest_ids = sorted(manifest.get("per_case", {}))
    if not (live_ids == manifest_ids == expected_ids):
        raise RedTeamRecordIntegrityError(
            "case-id sets disagree: "
            f"live={live_ids!r} manifest={manifest_ids!r} expected={expected_ids!r}. "
            f"{_CARDINAL}"
        )

    # 4. Canonical corpus hash
    live_payloads = redteam_corpus.redteam_payloads()
    live_corpus_hash = provenance.hash_corpus(live_payloads)
    if live_corpus_hash != manifest["corpus_hash"]:
        raise RedTeamRecordIntegrityError(
            f"corpus_hash mismatch: live={live_corpus_hash} manifest={manifest['corpus_hash']}."
            f" The red-team corpus content changed since it was frozen. {_CARDINAL}"
        )

    # 5. Canonical per-case hashes
    payload_by_id = dict(live_payloads)
    for cid in live_ids:
        live_hash = provenance.sha256_of(payload_by_id[cid])
        pinned_hash = manifest["per_case"][cid]["case_hash"]
        if live_hash != pinned_hash:
            raise RedTeamRecordIntegrityError(
                f"{cid}: case_hash mismatch (live={live_hash}, manifest={pinned_hash}). "
                f"{_CARDINAL}"
            )

    # 6. Live label facts == the entries this record will copy
    for cid in live_ids:
        case = redteam_corpus.load_redteam_case(cid)
        entry = manifest["per_case"][cid]
        mismatches = []
        if case.category != entry["category"]:
            mismatches.append("category")
        if case.label.appropriate_action != entry["appropriate_action"]:
            mismatches.append("appropriate_action")
        if case.label.external_dependency != entry["external_dependency"]:
            mismatches.append("external_dependency")
        if case.label.out_of_coverage_rationale != entry["out_of_coverage_rationale"]:
            mismatches.append("out_of_coverage_rationale")
        if mismatches:
            raise RedTeamRecordIntegrityError(
                f"{cid}: live label fields {mismatches} disagree with the frozen manifest "
                f"entry this record would copy. {_CARDINAL}"
            )

    # 7. observed_at_freeze is structurally complete
    observation = manifest.get("observed_at_freeze") or {}
    obs_per_case = observation.get("per_case") or []
    obs_ids = sorted(c.get("case_id") for c in obs_per_case)
    if (
        observation.get("case_count") != expected_count
        or observation.get("admitted_count") != expected_count
        or len(obs_per_case) != expected_count
        or obs_ids != expected_ids
    ):
        raise RedTeamRecordIntegrityError(
            "the frozen observed_at_freeze block is not structurally complete "
            f"(expected {expected_count} admitted entries covering {expected_ids}). "
            f"{_CARDINAL}"
        )
    for entry in obs_per_case:
        cid = entry.get("case_id")
        if entry.get("final_decision") != "approve":
            raise RedTeamRecordIntegrityError(
                f"{cid}: frozen observation final_decision is "
                f"{entry.get('final_decision')!r}, not 'approve'. {_CARDINAL}"
            )
        if entry.get("findings") != []:
            raise RedTeamRecordIntegrityError(
                f"{cid}: frozen observation carries findings {entry.get('findings')!r}, "
                f"expected none. {_CARDINAL}"
            )
        if entry.get("admitted") is not True:
            raise RedTeamRecordIntegrityError(
                f"{cid}: frozen observation admitted is {entry.get('admitted')!r}, not "
                f"True. {_CARDINAL}"
            )
        if "triage_present" not in entry or "triage_state" not in entry:
            raise RedTeamRecordIntegrityError(
                f"{cid}: frozen observation is missing triage_present or triage_state. "
                f"{_CARDINAL}"
            )

    # 8. Cross-link: per_case[cid].observed_at_freeze == observed_at_freeze.per_case entry
    obs_by_id = {c.get("case_id"): c for c in obs_per_case}
    for cid in live_ids:
        top_copy = manifest["per_case"][cid].get("observed_at_freeze")
        linked = obs_by_id.get(cid)
        if top_copy != linked:
            raise RedTeamRecordIntegrityError(
                f"{cid}: the manifest's per_case.observed_at_freeze does not match "
                f"observed_at_freeze.per_case for the same case — the manifest's two "
                f"copies disagree. {_CARDINAL}"
            )

    # 9. Offline-stub identity, frozen and live, both against the expected constant
    live_stub_identity = f"{PolicyMirrorStub.__module__}.{PolicyMirrorStub.__qualname__}"
    frozen_stub = manifest["pinned_config"]["offline_stub"]
    if frozen_stub != EXPECTED_STUB_IDENTITY or live_stub_identity != EXPECTED_STUB_IDENTITY:
        raise RedTeamRecordIntegrityError(
            f"offline-stub identity drift: frozen={frozen_stub!r}, "
            f"live={live_stub_identity!r}, expected={EXPECTED_STUB_IDENTITY!r}. "
            f"{_CONFIG_DRIFT}"
        )

    # 10. Promoted-rule ids
    live_rules = load_promoted_rules()
    live_rule_ids = sorted(r.rule_id for r in live_rules)
    frozen_rule_ids = sorted(manifest["pinned_config"]["promoted_rule_ids"])
    if live_rule_ids != frozen_rule_ids:
        raise RedTeamRecordIntegrityError(
            f"promoted-rule id drift: live={live_rule_ids!r}, frozen={frozen_rule_ids!r}. "
            f"{_CONFIG_DRIFT}"
        )

    # 11. Promoted-rules hash (catches a parameter change within an unchanged rule id)
    live_rules_hash = provenance.hash_promoted_rules([r.model_dump() for r in live_rules])
    frozen_rules_hash = manifest["pinned_config"]["promoted_rules_hash"]
    if live_rules_hash != frozen_rules_hash:
        raise RedTeamRecordIntegrityError(
            f"promoted-rules hash drift: live={live_rules_hash}, "
            f"frozen={frozen_rules_hash}. {_CONFIG_DRIFT}"
        )

    return manifest


# --------------------------------------------------------------------------------------
# Record content
# --------------------------------------------------------------------------------------
_SEPARATION_NOTE = (
    "Never averaged, aggregated, or co-rendered with the separate development-report "
    "surface or the separate in-coverage holdout corpus surface; those answer different "
    "questions and are published as separate artifacts."
)

_INVENTORY_NOTE = "Observed authoring outcome, not a quota, not a rate, not a distributional claim."

_CI_OMISSION_REASON = (
    "a confidence interval on a by-construction property would misrepresent that "
    "property as a sampled observation, the same principle by which "
    "SYSTEM-implies-no-approve carries no Wilson CI"
)

# Every entry is a NEGATED non-claim. "performance" and "effectiveness" appear only here,
# in negated form — the one place the no-metrics enforcement in the tests permits them.
_LIMITS = [
    "This record is not a deterministic-effectiveness, detection, robustness, or "
    "performance measurement.",
    "The frozen admission result is a by-construction property, not a false-approval "
    "rate; it carries no confidence interval, and it is not comparable to the empirical "
    "rate published for the P2 in-coverage holdout.",
    "This is a fixed, author-constructed synthetic challenge set, not a distributional "
    "sample. It supports no generalization, prevalence, or real-world residual-risk claim.",
    "Not an external, independent, or blinded benchmark; not a penetration test or "
    "adversarial assessment.",
    "Contains no live-provider observation. Whether the live triage layer raises concern "
    "on any case is a separate P4(c) measurement, never folded into this record.",
    "Contains no current-configuration observation: the per-case observations are "
    "historical, recorded at freeze, and were not re-observed for this record.",
    "Never averaged, aggregated, or co-rendered with the development-report surface or "
    "other separately published corpus artifacts.",
]

_PROVENANCE_NOTES = {
    "publication_commit_note": (
        "run.git.commit_sha identifies the code state that GENERATED this record. It "
        "does not identify the corpus."
    ),
    "frozen_bootstrap_note": (
        "corpus.frozen_git.head_commit_sha identifies the base commit the freeze ran "
        "against, with worktree_dirty=true because the corpus and its pin were frozen "
        "before their atomic commit."
    ),
    "anchor_note": (
        "Neither SHA is the corpus reproducibility anchor. corpus.corpus_hash over "
        "contract-normalized content is the sole corpus-content reproducibility anchor."
    ),
}


def _generate_run_id() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-redteam"


def _build_corpus_block(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": manifest["corpus_name"],
        "corpus_version": manifest["corpus_version"],
        "case_count": manifest["case_count"],
        "corpus_hash": manifest["corpus_hash"],
        "hash_method": manifest["hash_method"],
        "manifest_schema": manifest["schema"],
        "manifest_schema_version": manifest["schema_version"],
        "frozen_at_utc": manifest["provenance"]["frozen_at_utc"],
        "frozen_git": dict(manifest["git"]),
        "data_class": "synthetic",
        # Three separate facts, previously conflated into the single value "hand_authored":
        # who AUTHORED the case texts, how they were DERIVED, and the blindness context in
        # which the corpus was constructed. See ERRATA.md.
        "authoring": "llm_assisted_human_reviewed",
        "derivation": "curated_not_generator_drawn",
        "authoring_context": "author_constructed_non_blinded",
        "corpus_relationship": "fixed_synthetic_challenge_set",
        "coverage_scope": manifest["scope"],
        "scope_note": manifest["scope_note"],
        "separation_note": _SEPARATION_NOTE,
    }


def _build_pinned_config_block(manifest: dict[str, Any]) -> dict[str, Any]:
    pc = manifest["pinned_config"]
    return {
        "offline_stub": pc["offline_stub"],
        "promoted_rule_ids": list(pc["promoted_rule_ids"]),
        "promoted_rules_hash": pc["promoted_rules_hash"],
        # Always True: validate_frozen_contract() already raised above if it were not.
        "current_identity_verified": True,
    }


def _build_admission_block(manifest: dict[str, Any]) -> dict[str, Any]:
    a = manifest["admission"]
    return {
        "criterion": a["criterion"],
        "result": a["result"],
        "basis": "by_construction",
        "interpretation": a["interpretation"],
        "is_empirical_rate": False,
        "confidence_interval": None,
        "ci_omission_reason": _CI_OMISSION_REASON,
    }


def _build_inventory_block(manifest: dict[str, Any]) -> dict[str, Any]:
    per_case = manifest["per_case"]
    by_category: dict[str, int] = {}
    by_action = {"request_more_info": 0, "escalate": 0}
    for entry in per_case.values():
        by_category[entry["category"]] = by_category.get(entry["category"], 0) + 1
        by_action[entry["appropriate_action"]] = by_action.get(entry["appropriate_action"], 0) + 1
    deps = manifest["dependencies"]
    pinned_rule = sorted(deps.get("pinned_rule_parameter", []))
    pinned_lib = sorted(deps.get("pinned_library_behavior", []))
    none_count = len(per_case) - len(pinned_rule) - len(pinned_lib)
    return {
        "by_category": dict(sorted(by_category.items())),
        "by_appropriate_action": by_action,
        "by_external_dependency": {
            "none": none_count,
            "pinned_rule_parameter": pinned_rule,
            "pinned_library_behavior": pinned_lib,
        },
        "inventory_note": _INVENTORY_NOTE,
    }


def _build_per_case_list(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    per_case = manifest["per_case"]
    rows = []
    for cid in sorted(per_case):
        entry = per_case[cid]
        rows.append(
            {
                "case_id": cid,
                "category": entry["category"],
                "appropriate_action": entry["appropriate_action"],
                "external_dependency": entry["external_dependency"],
                "out_of_coverage_rationale": entry["out_of_coverage_rationale"],
                "case_hash": entry["case_hash"],
                "observation_kind": "historical_at_freeze",
                "observation_reobserved_for_this_record": False,
                "observed_at_freeze": copy.deepcopy(entry["observed_at_freeze"]),
            }
        )
    return rows


def _build_label_contract_block(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "label_basis": "threat_model_judgment",
        "label_review": "human_approved",
        "note": manifest["label_contract_note"],
        "not_externally_validated_truth": True,
    }


def build_admission_record() -> dict[str, Any]:
    """Build the complete P4(b) admission-and-provenance record in memory. Raises
    ``RedTeamRecordIntegrityError`` on any contract mismatch, before building anything.
    Performs NO agent execution, no ``scientific_report`` call, and no write — pure."""
    manifest = validate_frozen_contract()

    now = datetime.now(timezone.utc)
    run_block = provenance.build_run_provenance(
        run_type=RUN_TYPE,
        timestamp_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        run_id=_generate_run_id(),
    )

    return {
        "schema": RECORD_SCHEMA,
        "schema_version": RECORD_SCHEMA_VERSION,
        "run": run_block,
        "corpus": _build_corpus_block(manifest),
        "pinned_config": _build_pinned_config_block(manifest),
        "admission": _build_admission_block(manifest),
        "inventory": _build_inventory_block(manifest),
        "per_case": _build_per_case_list(manifest),
        "label_contract": _build_label_contract_block(manifest),
        "limits": list(_LIMITS),
        "provenance_notes": dict(_PROVENANCE_NOTES),
    }


# --------------------------------------------------------------------------------------
# Rendering — every qualifier precedes the figure it qualifies
# --------------------------------------------------------------------------------------
def _kv(label: str, value: Any) -> str:
    return f"- **{label}:** `{value}`"


def render_markdown(record: dict[str, Any]) -> str:
    """Human-readable rendering. Does NOT delegate to P5's ``scientific_report`` renderer,
    which exists to render five metric surfaces that do not exist here. Section order is
    load-bearing: limits precede any figure, and the sole ``30/30`` literal is followed
    immediately by its by-construction interpretation and CI-omission reason."""
    corpus = record["corpus"]
    pinned = record["pinned_config"]
    admission = record["admission"]
    inventory = record["inventory"]
    run = record["run"]
    notes = record["provenance_notes"]

    lines: list[str] = []
    lines.append(
        "# P4 Red-Team Corpus — Admission and Provenance Record (offline, out-of-coverage)"
    )
    lines.append("")
    lines.append(
        "> This record publishes the identity and provenance of the frozen P4 red-team "
        "corpus and its by-construction offline admission result. See Limits below for "
        "what this record is not."
    )
    lines.append("")

    lines.append("## Limits")
    lines.append("")
    for item in record["limits"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Corpus identity")
    lines.append("")
    lines.append(_kv("name", corpus["name"]))
    lines.append(_kv("corpus_version", corpus["corpus_version"]))
    lines.append(_kv("case_count", corpus["case_count"]))
    lines.append(_kv("hash_method", corpus["hash_method"]))
    lines.append(_kv("corpus_hash", corpus["corpus_hash"]))
    lines.append(f"  - {notes['anchor_note']}")
    fg = corpus["frozen_git"]
    lines.append(
        _kv(
            "frozen_git",
            f"head_commit_sha={fg.get('head_commit_sha')} branch={fg.get('branch')} "
            f"worktree_dirty={fg.get('worktree_dirty')}",
        )
    )
    lines.append(f"  - {notes['frozen_bootstrap_note']}")
    lines.append(_kv("frozen_at_utc", corpus["frozen_at_utc"]))
    lines.append(
        _kv("manifest_schema", f"{corpus['manifest_schema']} {corpus['manifest_schema_version']}")
    )
    lines.append(
        _kv(
            "data_class / authoring / derivation / authoring_context",
            f"{corpus['data_class']} / {corpus['authoring']} / {corpus['derivation']} / "
            f"{corpus['authoring_context']}",
        )
    )
    lines.append(_kv("corpus_relationship", corpus["corpus_relationship"]))
    lines.append(_kv("coverage_scope", corpus["coverage_scope"]))
    lines.append("")
    lines.append(f"**Scope.** {corpus['scope_note']}")
    lines.append("")
    lines.append(f"**Separation.** {corpus['separation_note']}")
    lines.append("")

    lines.append("## Pinned configuration")
    lines.append("")
    lines.append(_kv("offline_stub", pinned["offline_stub"]))
    lines.append(_kv("promoted_rule_ids", pinned["promoted_rule_ids"]))
    lines.append(_kv("promoted_rules_hash", pinned["promoted_rules_hash"]))
    lines.append(
        "- Current promoted-rule identity, promoted-rules hash, and offline-stub identity "
        "were independently reverified against this pin as a precondition of publishing "
        "this record."
    )
    lines.append("")

    lines.append("## Admission")
    lines.append("")
    lines.append(f"**{admission['criterion']}**")
    lines.append("")
    lines.append(f"Result: **{admission['result']}**")
    lines.append("")
    lines.append(admission["interpretation"])
    lines.append("")
    lines.append(f"*{admission['ci_omission_reason']}.*")
    lines.append("")

    lines.append("## Inventory")
    lines.append("")
    lines.append("By category:")
    for cat, n in inventory["by_category"].items():
        lines.append(f"- {cat}: {n}")
    lines.append("")
    lines.append("By appropriate action:")
    for action, n in inventory["by_appropriate_action"].items():
        lines.append(f"- {action}: {n}")
    lines.append("")
    dep = inventory["by_external_dependency"]
    lines.append("By external dependency:")
    lines.append(f"- none: {dep['none']}")
    lines.append(f"- pinned_rule_parameter: {dep['pinned_rule_parameter']}")
    lines.append(f"- pinned_library_behavior: {dep['pinned_library_behavior']}")
    lines.append("")
    lines.append(f"*{inventory['inventory_note']}*")
    lines.append("")

    lines.append("## Per-case (historical record, not re-observed)")
    lines.append("")
    lines.append(
        "The `observed_at_freeze` column below is copied unchanged from the frozen "
        "manifest. It is a historical observation recorded at freeze time and was NOT "
        "re-observed to publish this record."
    )
    lines.append("")
    lines.append("| case_id | category | action | dependency | case_hash (short) |")
    lines.append("|---|---|---|---|---|")
    for row in record["per_case"]:
        short_hash = row["case_hash"].split(":", 1)[-1][:12]
        lines.append(
            f"| {row['case_id']} | {row['category']} | {row['appropriate_action']} | "
            f"{row['external_dependency']} | `{short_hash}…` |"
        )
    lines.append("")

    lc = record["label_contract"]
    lines.append("## Label contract")
    lines.append("")
    lines.append(_kv("label_basis", lc["label_basis"]))
    lines.append(_kv("label_review", lc["label_review"]))
    lines.append(f"- {lc['note']}")
    lines.append("")

    lines.append("## Publication provenance")
    lines.append("")
    lines.append(_kv("run_id", run["run_id"]))
    lines.append(_kv("run_type", run["run_type"]))
    lines.append(_kv("timestamp_utc", run["timestamp_utc"]))
    git = run.get("git") or {}
    lines.append(_kv("commit_sha", git.get("commit_sha")))
    lines.append(f"  - {notes['publication_commit_note']}")
    lines.append(_kv("dirty", git.get("dirty")))
    lines.append(_kv("python_version", run["python_version"]))
    lines.append(_kv("harness_version", run["harness_version"]))
    lines.append("")
    lines.append(f"*{notes['anchor_note']}*")
    lines.append("")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------
# Validity predicate — narrow, membership/type only, not a general artifact reader
# --------------------------------------------------------------------------------------
def _is_valid_record_directory(path: Path) -> bool:
    """A directory is a valid admission record iff it contains EXACTLY two regular files
    at the two expected names, and no other entries. Rejects: missing, empty, one-file,
    extra-file, symlinked-expected-path, and directory-typed-expected-path directories.
    Never opens or parses file CONTENTS — membership and file-type only."""
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        entries = sorted(p.name for p in path.iterdir())
    except OSError:
        return False
    if entries != sorted([JSON_FILENAME, MD_FILENAME]):
        return False
    for name in (JSON_FILENAME, MD_FILENAME):
        p = path / name
        if p.is_symlink() or not p.is_file():
            return False
    return True


# --------------------------------------------------------------------------------------
# Publication — exclusive claim, no partial result, chained cleanup-failure error
# --------------------------------------------------------------------------------------
def _pre_claim_hook(final: Path) -> None:
    """No-op in production. Test seam only: a test may monkeypatch this to create a
    directory at ``final`` immediately before the exclusive claim, to exercise collision
    refusal under a simulated race between record-build and the directory claim."""
    return None


def publish(out_dir: str | Path = DEFAULT_OUT_DIR) -> Path:
    """Validate, build, render, and publish the P4(b) record as one atomic publication
    unit under a generated ``run_id`` directory.

    Guarantees:
    * On any integrity failure, nothing is created (``RedTeamRecordIntegrityError``).
    * A pre-existing directory at the target path is NEVER overwritten or reused, valid or
      not (``RecordCollisionError``); the exclusive ``os.mkdir`` claim leaves it untouched.
    * If a write fails after the claim, cleanup is attempted; if cleanup also fails, a
      chained ``RecordPublicationCleanupError`` is raised and the incomplete directory is
      left on disk, requiring manual investigation before any retry of this ``run_id``.
    * Only the claimed directory's removal is guaranteed. As a courtesy, a successful
      cleanup also attempts one non-recursive ``rmdir`` of the parent, but ONLY if this
      invocation created it; that attempt is best-effort and its failure is swallowed. A
      pre-existing parent, and any sibling inside it, is never touched.
    * A valid record is a directory containing exactly the two expected regular files;
      Markdown is written last, as the completion marker.
    """
    record = build_admission_record()  # raises RedTeamRecordIntegrityError; nothing created
    md_text = render_markdown(record)
    json_text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"

    out_root = Path(out_dir)
    out_root_preexisted = out_root.exists()
    out_root.mkdir(parents=True, exist_ok=True)  # ordinary, non-exclusive parent creation

    run_id = record["run"]["run_id"]
    final = out_root / run_id

    _pre_claim_hook(final)  # no-op in production; race-test seam only

    try:
        final.mkdir()  # THE exclusive claim: parents=False, exist_ok=False
    except FileExistsError as exc:
        raise RecordCollisionError(
            f"refusing to publish: {final} already exists. A published admission record "
            "is never overwritten or reused — re-run to publish a new sibling record, "
            "which coexists with this one over an unchanged corpus_hash. This is not a "
            "corpus version bump: the frozen corpus, its manifest, and its historical "
            "admission record are unchanged and are never edited or re-observed by this "
            "tool."
        ) from exc

    try:
        (final / JSON_FILENAME).write_text(json_text, encoding="utf-8")
        (final / MD_FILENAME).write_text(md_text, encoding="utf-8")  # completion marker
    except Exception as original_exc:
        try:
            shutil.rmtree(final)
        except Exception as cleanup_exc:
            raise RecordPublicationCleanupError(
                f"publishing {final} failed and automatic cleanup of that directory also "
                f"failed. {final} is incomplete and is NOT a valid admission record — it "
                "must never be read, rendered, or treated as published output. This "
                "requires manual investigation and removal before retrying publication "
                f"under this same run_id. Original publication failure: {original_exc!r}. "
                f"Cleanup failure: {cleanup_exc!r}.",
                original_error=original_exc,
                cleanup_error=cleanup_exc,
            ) from cleanup_exc
        if not out_root_preexisted:
            # Courtesy only, never a guarantee: the claimed directory is gone, so there is
            # no incomplete record and nothing to investigate. rmdir only — never recursive,
            # and never a parent that pre-existed. A failure here is benign (most commonly
            # ENOTEMPTY, because a concurrent publication landed a sibling in this shared
            # parent), so it is swallowed and the original write error propagates unchanged.
            try:
                out_root.rmdir()
            except OSError:
                pass
        raise

    assert _is_valid_record_directory(final), (
        f"internal error: {final} does not satisfy the record-validity predicate "
        "immediately after a successful publication"
    )
    return final


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Publish the P4(b) red-team admission-and-provenance record (never a "
            "scientific report, result, score, or performance artifact)."
        )
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT_DIR,
        help="parent output directory; a generated run-id leaf is always appended",
    )
    args = parser.parse_args(argv)

    final = publish(out_dir=args.out)
    print(f"published {final}")
    print(f"  {JSON_FILENAME}")
    print(f"  {MD_FILENAME}")


if __name__ == "__main__":
    main()

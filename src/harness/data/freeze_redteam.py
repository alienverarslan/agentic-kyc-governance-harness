"""Freeze tool: generate the P4 red-team corpus manifest.

``python -m harness.data.freeze_redteam``

This is the ONLY writer of ``redteam/manifest.json``. The manifest is a hash pin that tests
then enforce, so it must be produced by the same canonicalization the tests verify against
(``eval.provenance`` primitives, reused from P5) — a hand-written hash would be unverifiable
by construction.

**Status.** This tool has been run once, against the complete 30-fixture corpus, and produced
the initial and only ``redteam/manifest.json``. It will never write again: safeguard 2 below
refuses at any version. ``main`` still refuses unless exactly ``EXPECTED_CASE_COUNT`` canonical
``rt_NN`` fixtures are present, which is now a guard against a fixture being added or lost
rather than a gate on an unauthored corpus.

Safeguards (mirroring freeze_holdout, with a P4-specific admission gate)
------------------------------------------------------------------------
**1. All-or-nothing under the BLINDNESS GATE.** Every case must, under the pinned offline
deterministic configuration and promoted-rule set, produce a final ``approve`` with ZERO
findings of any origin (deterministic, system, or triage). If ANY case is caught — a finding
of any origin, or a non-approve decision — the freeze aborts nonzero and writes NOTHING. A
caught red-team case is a RESULT to investigate (a versioned re-triage), NEVER an edit to a
fixture or its label to make the number look right.

**2. Never overwrites.** An existing manifest refuses the freeze at any version; there is no
``--force``. The manifest is the single completion marker and is written last, atomically,
only after schema validation and the full blindness gate have passed.

**3. Initial freeze only.** No correction path, no migration path. A correction or a v2
requires a separately designed versioned-corpus migration that PRESERVES the previous
manifest and corpus.

**Cardinal rule:** a correction is NEVER used to make a caught case pass. Safeguard 1 makes
this enforceable rather than merely stated.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.agent.runner import run_agent
from harness.data import redteam_corpus
from harness.eval import provenance
from harness.llm.stub import PolicyMirrorStub
from harness.rules.store import load_promoted_rules

# Module-level so tests can redirect them without touching the real corpus.
MANIFEST_PATH = redteam_corpus.MANIFEST_PATH
EXPECTED_CASE_COUNT = redteam_corpus.EXPECTED_CASE_COUNT

MANIFEST_SCHEMA = "redteam_out_of_coverage_manifest"
MANIFEST_SCHEMA_VERSION = "1.0.0"
INITIAL_CORPUS_VERSION = "1"


class FreezeRefused(SystemExit):
    """Raised (as a nonzero exit) when a safeguard blocks the freeze. Distinct type so tests
    can assert the refusal precisely rather than matching on message text."""


# --------------------------------------------------------------------------------------
# Observation + the blindness-gate admission check
# --------------------------------------------------------------------------------------
def _normalize_triage(triage: Any) -> Any:
    """Complete, DETERMINISTIC snapshot of the triage object — so the record shows the
    offline triage was INERT (present but produced no finding), never findings alone.

    Supported forms are a Pydantic model (``model_dump(mode="json")``) or a plain dict; both
    canonicalize reproducibly. An unsupported type is REJECTED rather than ``repr()``-ed:
    ``repr`` can embed a memory address or other non-deterministic content, which must never
    enter a frozen provenance artifact. This raises inside ``_observe_redteam`` — before the
    manifest is built or written — so an unexpected triage type aborts the freeze cleanly.
    """
    if triage is None:
        return None
    if hasattr(triage, "model_dump"):
        return triage.model_dump(mode="json")
    if isinstance(triage, dict):
        return triage
    raise TypeError(
        f"unsupported triage type for frozen observation: {type(triage).__qualname__}; "
        "refusing to write non-deterministic repr() into a provenance artifact"
    )


def _observe_redteam() -> dict[str, Any]:
    """Screen every red-team case under the exact pinned offline config and snapshot the
    COMPLETE observed output: final decision, complete finding set (with origins),
    triage_present, and the normalized triage state. Recording findings alone would be
    incomplete when the offline path demonstrably returns an inert triage object."""
    stub = PolicyMirrorStub()
    promoted = load_promoted_rules()
    per_case: list[dict[str, Any]] = []

    for case_id in redteam_corpus.list_redteam_ids():
        case = redteam_corpus.load_redteam_case(case_id)
        result = run_agent(case.dossier, stub, case_ref=case_id, promoted_rules=promoted)
        findings = sorted(
            ({"origin": f.origin, "code": f.code} for f in result.findings),
            key=lambda d: (d["origin"], d["code"]),
        )
        per_case.append(
            {
                "case_id": case_id,
                "appropriate_action": case.label.appropriate_action,
                "final_decision": result.decision,
                "findings": findings,
                "triage_present": result.triage is not None,
                "triage_state": _normalize_triage(result.triage),
                "admitted": result.decision == "approve" and not result.findings,
            }
        )

    admitted = [c["case_id"] for c in per_case if c["admitted"]]
    return {
        "note": (
            "complete observed output under the pinned offline config + promoted rules; the "
            "blindness gate admits a case only on final approve with zero findings of any "
            "origin. A present-but-inert triage object is recorded state, not detection."
        ),
        "case_count": len(per_case),
        "admitted_count": len(admitted),
        "per_case": per_case,
    }


def inadmissible_case_ids(observation: dict[str, Any]) -> list[str]:
    """Case ids that fail the blindness gate (non-approve, or any finding of any origin)."""
    return [c["case_id"] for c in observation["per_case"] if not c["admitted"]]


def validate_admission(observation: dict[str, Any]) -> None:
    """Refuse to freeze unless EVERY case is admitted by the blindness gate. Raises BEFORE
    anything is written — this is what makes the cardinal rule enforceable."""
    bad = inadmissible_case_ids(observation)
    n = observation["case_count"]
    if bad or observation["admitted_count"] != n:
        detail = ", ".join(bad) if bad else "(admitted_count disagrees with per-case data)"
        raise FreezeRefused(
            "refusing to freeze: these red-team cases were CAUGHT by the deterministic layer "
            f"under the pinned offline config and are inadmissible: {detail}. No manifest was "
            "written. A caught case is a RESULT (a versioned re-triage) — investigate; never "
            "relabel or edit a fixture to preserve the corpus."
        )


# --------------------------------------------------------------------------------------
# Payload builder
# --------------------------------------------------------------------------------------
def _pinned_config() -> dict[str, Any]:
    rules = [r.model_dump() for r in load_promoted_rules()]
    return {
        "offline_stub": PolicyMirrorStub.__module__ + "." + PolicyMirrorStub.__qualname__,
        "promoted_rule_ids": [r["rule_id"] for r in rules],
        "promoted_rules_hash": provenance.hash_promoted_rules(rules),
    }


def _git_provenance() -> dict[str, Any]:
    """P4-specific git block: the ACTUAL full branch HEAD at freeze, plus branch and dirty
    state — not an abbreviated reference to the original base commit."""
    info = provenance.git_info()
    return {
        "head_commit_sha": info["commit_sha"],
        "branch": provenance.git_branch(),
        "worktree_dirty": info["dirty"],
    }


def build_manifest(corpus_version: str) -> dict[str, Any]:
    payloads = redteam_corpus.redteam_payloads()
    observation = _observe_redteam()
    now = datetime.now(timezone.utc)

    by_dep: dict[str, list[str]] = {"pinned_rule_parameter": [], "pinned_library_behavior": []}
    per_case: dict[str, Any] = {}
    obs_by_id = {c["case_id"]: c for c in observation["per_case"]}
    for cid, payload in payloads:
        label = payload["label"]
        dep = label["external_dependency"]
        if dep in by_dep:
            by_dep[dep].append(cid)
        per_case[cid] = {
            "case_hash": provenance.sha256_of(payload),
            "category": payload["category"],
            "appropriate_action": label["appropriate_action"],
            "external_dependency": dep,
            "out_of_coverage_rationale": label["out_of_coverage_rationale"],
            "observed_at_freeze": obs_by_id.get(cid),
        }

    return {
        "schema": MANIFEST_SCHEMA,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "corpus_name": "redteam_out_of_coverage",
        "corpus_version": corpus_version,
        "scope": "out_of_coverage",
        "scope_note": (
            "Hand-authored synthetic threat-model concerns, each falling OUTSIDE every "
            "deterministic check's declared scope under the pinned config. Deterministic "
            "approval of admitted cases is an intentional corpus-admission property, not a "
            "violation of the in-coverage FAR invariant, and is not reported as an empirical "
            "rate (that invariant is scoped to in-coverage corpora)."
        ),
        "label_contract_note": (
            "Each label is a human-reviewed threat-model judgment about the appropriate "
            "action; it is NOT externally-validated ground truth."
        ),
        "case_count": len(payloads),
        "corpus_hash": provenance.hash_corpus(payloads),
        "hash_method": (
            "sha256 over canonical json (sorted keys, compact separators, utf-8) of each "
            "case's {case_id, category, dossier, label} AFTER contract-model normalization, "
            "ordered by case_id. Excludes timestamps, git metadata, freeze observations, the "
            "manifest itself, and all raw-byte formatting."
        ),
        "pinned_config": _pinned_config(),
        "admission": {
            "criterion": (
                "final approve; zero findings of any origin; under the pinned offline "
                "config + promoted-rule set"
            ),
            "result": f"{observation['admitted_count']}/{observation['case_count']}",
            "interpretation": (
                "by_construction — every admitted case is required to approve with no "
                "findings, so this is NOT an empirical false-approval rate and carries no "
                "confidence interval. The meaningful empirical numbers are the separate live "
                "full-stack measurements (P4(c))."
            ),
        },
        "dependencies": by_dep,
        "per_case": per_case,
        "git": _git_provenance(),
        "provenance": {
            "frozen_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "python": provenance.python_version(),
            "harness_version": provenance.harness_version(),
            "packages": provenance.package_versions(),
        },
        "observed_at_freeze": observation,
        # Reserved for a future versioned-corpus migration that PRESERVES the previous
        # manifest and corpus. P4 is initial-freeze-only and never populates this.
        "corrections": [],
    }


# --------------------------------------------------------------------------------------
# Writing (atomic, and only after everything is built and validated)
# --------------------------------------------------------------------------------------
def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Serialize to a temp file in the target directory, then atomically replace, so a
    failure never leaves a half-written manifest (which would be a hash file that hashes
    nothing)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _validate_all_schemas(case_ids: list[str]) -> None:
    """Force full pydantic + integrity validation of every fixture BEFORE observing or
    writing. A schema/integrity error must abort the freeze, writing nothing."""
    for cid in case_ids:
        redteam_corpus.load_redteam_case(cid)  # raises on schema or id/category mismatch


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Freeze the P4 out-of-coverage red-team corpus (initial freeze only)."
    )
    parser.add_argument("--corpus-version", default=INITIAL_CORPUS_VERSION)
    args = parser.parse_args(argv)

    # Safeguard 2 + 3: never overwrite, no correction/migration path. The requested version
    # is irrelevant — an existing manifest always refuses.
    if MANIFEST_PATH.exists():
        raise FreezeRefused(
            f"refusing to overwrite the existing frozen manifest at {MANIFEST_PATH}. The "
            "corpus is frozen and this tool never replaces a manifest — at any version. P4 "
            "supports INITIAL FREEZE ONLY. A correction, or a v2, requires a separately "
            "designed versioned-corpus migration that PRESERVES this manifest and corpus. A "
            "correction is NEVER used to make a caught case pass."
        )

    ids = redteam_corpus.list_redteam_ids()
    if len(ids) != EXPECTED_CASE_COUNT:
        raise FreezeRefused(
            f"expected exactly {EXPECTED_CASE_COUNT} redteam fixtures, found {len(ids)}; "
            "refusing. (The frozen corpus is exactly this many cases; a differing count means a "
            "fixture was added or lost, which is a RESULT to investigate, not a freeze to force.)"
        )
    if ids != [f"rt_{i:02d}" for i in range(1, EXPECTED_CASE_COUNT + 1)]:
        raise FreezeRefused(
            f"redteam ids are not the contiguous set rt_01..rt_{EXPECTED_CASE_COUNT:02d}: {ids}"
        )

    # Schema/integrity validation, THEN build (which observes), THEN the blindness gate,
    # THEN the single atomic write of the completion marker. Any failure writes nothing.
    _validate_all_schemas(ids)
    manifest = build_manifest(args.corpus_version)
    validate_admission(manifest["observed_at_freeze"])
    _atomic_write(MANIFEST_PATH, manifest)

    print(f"wrote {MANIFEST_PATH}")
    print(f"  corpus_version : {manifest['corpus_version']}")
    print(f"  case_count     : {manifest['case_count']}")
    print(f"  corpus_hash    : {manifest['corpus_hash']}")
    print(f"  admission      : {manifest['admission']['result']} (by construction; not a FAR)")
    print(f"  pinned rules   : {manifest['pinned_config']['promoted_rule_ids']} "
          f"{manifest['pinned_config']['promoted_rules_hash']}")


if __name__ == "__main__":
    main()

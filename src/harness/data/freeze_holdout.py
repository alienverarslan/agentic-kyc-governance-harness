"""Freeze tool: generate the holdout manifest and the seed hash-pin.

``python -m harness.data.freeze_holdout``

This is the ONLY writer of ``holdout/manifest.json`` and ``seed/hash_pin.json``. Both are
hash pins that tests then enforce, so they must be produced by the same canonicalization
the tests verify against (``eval.provenance.sha256_of`` / ``hash_corpus``, reused verbatim
from P5) — a hand-written hash would be unverifiable by construction.

Three safeguards, all of them load-bearing
------------------------------------------
**1. Freezing is all-or-nothing.** If ANY holdout case's observed decision, finding set, or
trajectory disagrees with its declared truth, the tool aborts nonzero and writes NEITHER
file. Recording the disagreement and freezing anyway would produce an authoritative-looking
manifest that is actually a record of failure — and would pin a corpus the engine does not
satisfy. This mirrors P5's rule that an integrity failure emits no partial artifact.

**2. The tool never overwrites an existing manifest.** Not at the same version, not at a
different one, and there is no ``--force``. A frozen corpus that a script can silently
replace is not frozen. A legitimate correction requires deliberately DELETING the manifest
first, which appears in git as a deletion alongside its replacement — a reviewable act
rather than an invisible side effect of re-running a command.

**2b. The manifest is the COMPLETION MARKER, and is written last.** ``os.replace`` makes
each individual write atomic, but a two-file write is not transactional: a process killed
between them would otherwise leave a half-finished freeze. Because the manifest is both the
last write and the thing safeguard 2 keys on, any interruption before it completes leaves NO
marker — so a rerun is permitted and simply regenerates the seed pin. The invariant is:
*if the manifest exists, both pins were written.* This is a recoverable protocol, not pair
atomicity, and it is deliberately not described as pair atomicity anywhere.

**3. P2 supports INITIAL FREEZE ONLY.** This tool has exactly one job: freeze a corpus that
has never been frozen. It has no correction path, no migration path, and no way to produce
a second version — those are deliberately out of scope.

Corrections and a holdout v2 require a separately designed, versioned-corpus migration that
**preserves the previous manifest and the previous corpus** rather than replacing them. That
is a different problem from freezing (it must keep both versions readable, record what moved
between them, and let a reader reproduce either), and building a half-version of it here
would create exactly the silent-replacement hole this tool exists to prevent. The
``corrections`` key in the manifest is written as an empty list and reserved for that future
design; nothing in P2 populates it.

**Cardinal rule:** a correction is NEVER used to make a failing case pass. If the harness
disagrees with a holdout case, that is a RESULT — investigate the harness. Editing the case
to match current behavior converts the holdout into a mirror of the code it exists to test,
destroying the only thing it measures. Safeguard 1 is what makes this rule enforceable
rather than merely stated: you cannot quietly re-freeze around a failure.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.agent.runner import run_agent
from harness.data import holdout_corpus
from harness.data.loader import SEED_DIR, list_seed_ids, load_case
from harness.eval import provenance
from harness.eval.metrics import agent_finding_codes, decision_match, finding_match, trajectory_match
from harness.eval.stats import RateStat
from harness.llm.stub import PolicyMirrorStub
from harness.rules.store import load_promoted_rules

# Module-level so tests can redirect them without touching the real corpus.
MANIFEST_PATH = holdout_corpus.MANIFEST_PATH
SEED_PIN_PATH = SEED_DIR / "hash_pin.json"

MANIFEST_SCHEMA_VERSION = "1.0.0"
INITIAL_CORPUS_VERSION = "1.0.0"


class FreezeRefused(SystemExit):
    """Raised (as a nonzero exit) when a safeguard blocks the freeze. Distinct type so
    tests can assert the refusal precisely rather than matching on message text."""


# --------------------------------------------------------------------------------------
# Observation + the all-or-nothing gate
# --------------------------------------------------------------------------------------
def _observe_holdout() -> dict[str, Any]:
    """Screen every holdout case offline and snapshot what the harness ACTUALLY did.

    Recorded as an ``observed_at_freeze`` snapshot, not as an expectation: the per-case
    ground truth in each fixture is the expectation, this is the observation. It exists so
    a future reader can tell whether behavior moved between freeze time and their run.
    """
    stub = PolicyMirrorStub()
    promoted = load_promoted_rules()
    per_case: list[dict[str, Any]] = []

    for case_id in holdout_corpus.list_holdout_ids():
        case = holdout_corpus.load_holdout_case(case_id)
        result = run_agent(case.dossier, stub, case_ref=case_id, promoted_rules=promoted)
        truth = case.decision_truth
        per_case.append(
            {
                "case_id": case_id,
                "expected_decision": truth.expected_decision,
                "observed_decision": result.decision,
                "expected_codes": sorted(truth.injected_codes),
                "observed_codes": sorted(agent_finding_codes(result)),
                "decision_match": decision_match(result, truth),
                "finding_match": finding_match(result, truth),
                "trajectory_match": trajectory_match(result, truth),
            }
        )

    non_approve = [c for c in per_case if c["expected_decision"] != "approve"]
    false_approvals = [c["case_id"] for c in non_approve if c["observed_decision"] == "approve"]

    return {
        "note": (
            "snapshot of harness behavior at freeze time; the per-case decision_truth in "
            "each fixture is the expectation, this is the observation"
        ),
        "decision_match_count": sum(c["decision_match"] for c in per_case),
        "finding_match_count": sum(c["finding_match"] for c in per_case),
        "trajectory_match_count": sum(c["trajectory_match"] for c in per_case),
        "case_count": len(per_case),
        # RateStat supplies k, n, point_estimate and the Wilson 95% CI from P5's stats
        # module. House style from P5 onward: a rate is never published as a bare figure —
        # it carries its raw counts AND its interval, so 0/14's small-n uncertainty is
        # visible at the point of publication rather than deferred to a later artifact.
        "false_approval_rate": {
            **RateStat(len(false_approvals), len(non_approve)).to_dict(),
            "cases": false_approvals,
            "interpretation": (
                "EMPIRICAL, corpus-scoped rate — it depends on the deterministic checks "
                "actually catching each authored case, and is not a structural guarantee. "
                "The Wilson 95% CI quantifies sampling uncertainty at this corpus size; it "
                "does not extend the claim beyond this corpus. The only structural "
                "invariant is SYSTEM-origin-implies-no-approve."
            ),
        },
        "per_case": per_case,
    }


def mismatching_case_ids(observation: dict[str, Any]) -> list[str]:
    """Case ids whose observed behavior differs from declared truth on ANY of the three
    axes. Pure — takes a snapshot, returns ids, touches nothing."""
    return [
        c["case_id"]
        for c in observation["per_case"]
        if not (c["decision_match"] and c["finding_match"] and c["trajectory_match"])
    ]


def validate_observation(observation: dict[str, Any]) -> None:
    """Refuse to freeze unless every case matches on all three axes.

    This is the safeguard that makes the cardinal rule enforceable: a failing case cannot
    be quietly frozen into the record. It raises BEFORE anything is written.
    """
    mismatches = mismatching_case_ids(observation)
    n = observation["case_count"]
    counts_agree = (
        observation["decision_match_count"] == n
        and observation["finding_match_count"] == n
        and observation["trajectory_match_count"] == n
    )
    if mismatches or not counts_agree:
        detail = ", ".join(mismatches) if mismatches else "(counts disagree with per-case data)"
        raise FreezeRefused(
            "refusing to freeze: the deterministic engine disagrees with declared holdout "
            f"truth for {detail}. No manifest and no seed pin were written. This is a "
            "RESULT, not a chore: investigate the harness. A correction is never used to "
            "make a failing case pass."
        )


# --------------------------------------------------------------------------------------
# Payload builders
# --------------------------------------------------------------------------------------
def build_manifest(corpus_version: str) -> dict[str, Any]:
    payloads = holdout_corpus.holdout_payloads()
    now = datetime.now(timezone.utc)

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "corpus_name": "deterministic_holdout",
        "corpus_version": corpus_version,
        "frozen_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope": "in_coverage",
        "scope_note": (
            "Novel hand-authored cases whose issues fall INSIDE the deterministic checks' "
            "existing coverage. Measures generalization to novel cases, not to novel "
            "phenomena; out-of-coverage generalization is P4."
        ),
        "authoring_note": (
            "Hand-authored, never drawn from the synthetic generator: a different generator "
            "seed would test reproducibility, not generalization. Independence is sought in "
            "cities, districts, streets, sectors, company cores, personal names, legal-suffix "
            "surface forms, and ownership structures, none of which are taken from "
            "harness/generate/pools.py."
        ),
        "case_count": len(payloads),
        "corpus_hash": provenance.hash_corpus(payloads),
        "hash_method": (
            "sha256 over canonical json (sorted keys, compact separators, utf-8) of each "
            "case's {dossier, decision_truth} AFTER contract-model normalization, ordered "
            "by case_id; invariant to whitespace/key-order/line-endings, sensitive to every "
            "meaning-bearing change"
        ),
        "cases": [
            {
                "case_id": cid,
                "sha256": provenance.sha256_of(payload),
                "injected_codes": payload["decision_truth"]["injected_codes"],
                "expected_decision": payload["decision_truth"]["expected_decision"],
            }
            for cid, payload in payloads
        ],
        "git": provenance.git_info(),
        "observed_at_freeze": _observe_holdout(),
        # Reserved for a future versioned-corpus migration that PRESERVES the previous
        # manifest and corpus. P2 is initial-freeze-only and never populates this.
        "corrections": [],
    }


def build_seed_pin() -> dict[str, Any]:
    """Hash-pin for the 8 seed fixtures.

    Closes a gap P2 motivated: "the 8 seed fixtures are never edited" has been a documented
    invariant since slice-1 but was never mechanically enforced. Same canonicalization as
    the holdout pin.
    """
    payloads: list[tuple[str, dict[str, Any]]] = []
    for case_id in list_seed_ids():
        case = load_case(case_id)
        payloads.append(
            (
                case_id,
                {
                    "dossier": case.dossier.model_dump(mode="json"),
                    "decision_truth": case.decision_truth.model_dump(mode="json"),
                },
            )
        )

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "corpus_name": "seed",
        "note": (
            "Enforces the long-standing 'the 8 seed fixtures are never edited' invariant. "
            "If a seed case must genuinely change, that is a deliberate act requiring this "
            "pin to be regenerated in the same commit, with the reason recorded."
        ),
        "case_count": len(payloads),
        "corpus_hash": provenance.hash_corpus(payloads),
        "cases": [
            {"case_id": cid, "sha256": provenance.sha256_of(payload)} for cid, payload in payloads
        ],
    }


# --------------------------------------------------------------------------------------
# Writing (atomic, and only after everything is built and validated)
# --------------------------------------------------------------------------------------
def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Serialize to a temp file in the target directory, then atomically replace.

    A half-written pin is worse than none: it would be a hash file that hashes nothing.
    This makes each FILE atomic; the two-file sequence is made recoverable by write ORDER
    (see the completion-marker note in the module docstring), not by pretending the pair is
    transactional.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Freeze the P2 holdout corpus (initial freeze only)."
    )
    parser.add_argument("--corpus-version", default=INITIAL_CORPUS_VERSION)
    args = parser.parse_args(argv)

    # Safeguard 2 + 3: never overwrite, and no correction/migration path exists. The
    # requested version is irrelevant here — an existing manifest always refuses.
    if MANIFEST_PATH.exists():
        raise FreezeRefused(
            f"refusing to overwrite the existing frozen manifest at {MANIFEST_PATH}. "
            "The corpus is frozen and this tool never replaces a manifest — at any version. "
            "P2 supports INITIAL FREEZE ONLY: it has no correction or migration path. "
            "Correcting a case, or producing a holdout v2, requires a separately designed "
            "versioned-corpus migration that PRESERVES this manifest and corpus rather than "
            "replacing them. A correction is NEVER used to make a failing case pass."
        )

    # Build BOTH payloads and validate BEFORE writing either (no partial paired writes).
    seed_pin = build_seed_pin()
    if seed_pin["case_count"] != 8:
        raise FreezeRefused(
            f"expected 8 seed fixtures, found {seed_pin['case_count']}; not writing any pin."
        )

    manifest = build_manifest(args.corpus_version)

    # Safeguard 1: all-or-nothing. Raises before the first write.
    validate_observation(manifest["observed_at_freeze"])

    # ORDER IS LOAD-BEARING: the seed pin first, the manifest LAST as the completion
    # marker. Safeguard 2 keys on the manifest's existence, so an interruption before this
    # final write leaves no marker and a rerun recovers by regenerating the seed pin — no
    # manual deletion required. Reversing these two lines reintroduces an unrecoverable
    # half-frozen state.
    _atomic_write(SEED_PIN_PATH, seed_pin)
    _atomic_write(MANIFEST_PATH, manifest)

    obs = manifest["observed_at_freeze"]
    far = obs["false_approval_rate"]
    print(f"wrote {MANIFEST_PATH}")
    print(f"  corpus_version : {manifest['corpus_version']}")
    print(f"  case_count     : {manifest['case_count']}")
    print(f"  corpus_hash    : {manifest['corpus_hash']}")
    print("  observed at freeze (all three must equal case_count, or nothing is written):")
    print(f"    decision_match   : {obs['decision_match_count']}/{obs['case_count']}")
    print(f"    finding_match    : {obs['finding_match_count']}/{obs['case_count']}")
    print(f"    trajectory_match : {obs['trajectory_match_count']}/{obs['case_count']}")
    ci = far["wilson_ci_95"]
    ci_s = "N/A" if ci is None else f"[{ci[0] * 100:.1f}%, {ci[1] * 100:.1f}%]"
    print(
        f"    false_approvals  : {far['k']}/{far['n']}  {far['cases']}  "
        f"Wilson 95% CI {ci_s}  [empirical, corpus-scoped]"
    )
    print(f"wrote {SEED_PIN_PATH}")
    print(f"  seed corpus_hash : {seed_pin['corpus_hash']}")


if __name__ == "__main__":
    main()

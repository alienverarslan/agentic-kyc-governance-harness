"""P2 — immutability: the holdout and the seed fixtures are hash-pinned.

A holdout that can be quietly edited is not a holdout. These tests make "frozen" a
mechanically enforced property rather than a documented intention.

The seed pin closes a gap P2 motivated: "the 8 seed fixtures are never edited" has been an
invariant since slice-1, but until now it was documented only. Any edit to a seed fixture
— including a well-meaning one made to get a test passing — now fails here.

**Cardinal rule, restated where it is enforced:** a hash mismatch is a RESULT, not a
chore. It is never resolved by regenerating the pin to match. If a fixture genuinely must
change, that is a deliberate act: bump ``corpus_version``, record a ``corrections`` entry
explaining what and why, and regenerate in the same commit. A correction is never used to
make a failing case pass.
"""

from __future__ import annotations

import pytest

from harness.data.holdout_corpus import (
    MANIFEST_PATH,
    case_payload,
    holdout_payloads,
    list_holdout_ids,
    load_manifest,
)
from harness.data.loader import SEED_DIR, list_seed_ids, load_case
from harness.eval.provenance import hash_corpus, sha256_of

SEED_PIN_PATH = SEED_DIR / "hash_pin.json"

_REGEN = (
    "If this change is intended, regenerate with "
    "`python -m harness.data.freeze_holdout --corpus-version <NEW>` and record a "
    "`corrections` entry. Never regenerate merely to silence this test."
)


# --------------------------------------------------------------------------------------
# Holdout pin
# --------------------------------------------------------------------------------------
def test_holdout_manifest_exists():
    assert MANIFEST_PATH.exists(), (
        f"holdout manifest missing at {MANIFEST_PATH}; "
        "run `python -m harness.data.freeze_holdout`"
    )


def test_holdout_corpus_hash_matches_manifest():
    manifest = load_manifest()
    assert hash_corpus(holdout_payloads()) == manifest["corpus_hash"], (
        f"the holdout corpus content changed since it was frozen. {_REGEN}"
    )


@pytest.mark.parametrize("case_id", list_holdout_ids())
def test_holdout_case_hash_matches_manifest(case_id: str):
    """Per-case pins localize a drift to the exact fixture that moved, instead of only
    telling you that something, somewhere, changed."""
    pinned = {c["case_id"]: c["sha256"] for c in load_manifest()["cases"]}
    assert case_id in pinned, f"{case_id} is not in the manifest. {_REGEN}"
    assert sha256_of(case_payload(case_id)) == pinned[case_id], (
        f"{case_id} changed since it was frozen. {_REGEN}"
    )


def test_manifest_case_set_matches_the_files_on_disk():
    """Catches both directions: a fixture added without re-freezing, and a fixture deleted
    while its manifest entry lingers."""
    manifest_ids = sorted(c["case_id"] for c in load_manifest()["cases"])
    assert manifest_ids == list_holdout_ids(), _REGEN


def test_manifest_records_its_freeze_contract():
    manifest = load_manifest()
    assert manifest["corpus_name"] == "deterministic_holdout"
    assert manifest["scope"] == "in_coverage"
    assert manifest["corpus_version"]
    assert isinstance(manifest["corrections"], list)
    assert manifest["case_count"] == len(list_holdout_ids())


def test_manifest_labels_holdout_far_as_empirical_not_structural():
    """Guards the framing itself. The observed false_approval_rate on this corpus is an
    empirical rate; only SYSTEM-implies-no-approve is structural. If someone later edits the
    manifest builder to call this a guarantee, this test fails."""
    far = load_manifest()["observed_at_freeze"]["false_approval_rate"]
    interpretation = far["interpretation"].lower()
    assert "empirical" in interpretation
    assert "not a structural guarantee" in interpretation
    assert far["k"] == 0


def test_the_published_far_carries_its_counts_and_wilson_interval():
    """House style from P5 onward: a rate is never published bare. The frozen manifest is a
    publication surface, so k, n, the point estimate and the Wilson 95% CI must all be
    present in the artifact itself — not only in a downstream report."""
    far = load_manifest()["observed_at_freeze"]["false_approval_rate"]
    for key in ("k", "n", "point_estimate", "wilson_ci_95"):
        assert key in far, f"published rate is missing {key!r}"
    lo, hi = far["wilson_ci_95"]
    assert 0.0 <= lo <= hi <= 1.0
    assert hi > 0.0, "k=0 must not degenerate to a zero-width interval"


# --------------------------------------------------------------------------------------
# Seed pin (the previously documented-only invariant)
# --------------------------------------------------------------------------------------
def _seed_payload(case_id: str) -> dict:
    case = load_case(case_id)
    return {
        "dossier": case.dossier.model_dump(mode="json"),
        "decision_truth": case.decision_truth.model_dump(mode="json"),
    }


def test_seed_pin_exists():
    assert SEED_PIN_PATH.exists(), (
        f"seed hash pin missing at {SEED_PIN_PATH}; run `python -m harness.data.freeze_holdout`"
    )


def test_seed_corpus_is_unchanged():
    import json

    pin = json.loads(SEED_PIN_PATH.read_text(encoding="utf-8"))
    payloads = [(cid, _seed_payload(cid)) for cid in list_seed_ids()]
    assert hash_corpus(payloads) == pin["corpus_hash"], (
        "a seed fixture changed. The 8 seed fixtures are read-only ground truth and have "
        "been since slice-1: if a seed case fails, fix the check logic, not the fixture."
    )


@pytest.mark.parametrize("case_id", list_seed_ids())
def test_seed_case_is_unchanged(case_id: str):
    import json

    pin = json.loads(SEED_PIN_PATH.read_text(encoding="utf-8"))
    pinned = {c["case_id"]: c["sha256"] for c in pin["cases"]}
    assert case_id in pinned
    assert sha256_of(_seed_payload(case_id)) == pinned[case_id], (
        f"seed fixture {case_id} changed; seeds are read-only ground truth."
    )


def test_there_are_still_exactly_eight_seed_fixtures():
    assert len(list_seed_ids()) == 8

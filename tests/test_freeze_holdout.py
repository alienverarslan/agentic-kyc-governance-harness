"""P2 — the freeze tool's safeguards.

The freeze tool is the only writer of the two hash pins, which makes it the single point
where the corpus's frozen-ness can be subverted. These tests cover the three ways that
could happen:

1. freezing a corpus the engine does not actually satisfy (mismatch must abort, and must
   leave NO partial output);
2. silently replacing an existing frozen record (must be impossible, at any version);
3. producing a second corpus version at all — P2 is INITIAL FREEZE ONLY. There is no
   correction path and no migration path here by design; those require a separately
   designed versioned-corpus migration that preserves the previous manifest and corpus.

Every test redirects the tool's output paths into ``tmp_path``, so nothing here can touch
the real frozen corpus.
"""

from __future__ import annotations

import json

import pytest

from harness.data import freeze_holdout
from harness.data.holdout_corpus import holdout_payloads, list_holdout_ids
from harness.data.loader import list_seed_ids, load_case
from harness.eval.provenance import hash_corpus, sha256_of


def _fresh_seed_payloads():
    """Recompute the seed corpus payloads independently of the freeze tool, so the
    recovery assertions compare against a freshly derived truth rather than against
    whatever the tool happened to write."""
    payloads = []
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
    return payloads


@pytest.fixture
def sandboxed(tmp_path, monkeypatch):
    """Point both pin paths at a temp directory and return them."""
    manifest = tmp_path / "holdout" / "manifest.json"
    seed_pin = tmp_path / "seed" / "hash_pin.json"
    monkeypatch.setattr(freeze_holdout, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(freeze_holdout, "SEED_PIN_PATH", seed_pin)
    return manifest, seed_pin


def _observation(case_ids, *, break_case: str | None = None, axis: str = "decision_match"):
    """A synthetic observation snapshot, optionally with one case failing one axis."""
    per_case = []
    for cid in case_ids:
        entry = {
            "case_id": cid,
            "expected_decision": "approve",
            "observed_decision": "approve",
            "expected_codes": ["NONE"],
            "observed_codes": ["NONE"],
            "decision_match": True,
            "finding_match": True,
            "trajectory_match": True,
        }
        if cid == break_case:
            entry[axis] = False
        per_case.append(entry)

    n = len(per_case)
    return {
        "case_count": n,
        "decision_match_count": sum(c["decision_match"] for c in per_case),
        "finding_match_count": sum(c["finding_match"] for c in per_case),
        "trajectory_match_count": sum(c["trajectory_match"] for c in per_case),
        "false_approval_rate": {"k": 0, "n": 0, "cases": [], "interpretation": "empirical"},
        "per_case": per_case,
    }


# --------------------------------------------------------------------------------------
# Safeguard 1 — all-or-nothing
# --------------------------------------------------------------------------------------
def test_validate_observation_accepts_a_fully_matching_snapshot():
    freeze_holdout.validate_observation(_observation(["hold_01", "hold_02"]))


@pytest.mark.parametrize("axis", ["decision_match", "finding_match", "trajectory_match"])
def test_validate_observation_refuses_on_any_axis(axis: str):
    """A disagreement on ANY of the three axes blocks the freeze — not just the decision."""
    obs = _observation(["hold_01", "hold_02"], break_case="hold_02", axis=axis)
    with pytest.raises(freeze_holdout.FreezeRefused) as exc:
        freeze_holdout.validate_observation(obs)
    assert "hold_02" in str(exc.value)
    assert "hold_01" not in str(exc.value)


def test_freeze_writes_nothing_when_a_case_mismatches(sandboxed, monkeypatch):
    """No partial output: neither the manifest nor the seed pin may exist after a refusal.

    The seed pin is built BEFORE the manifest, so this is the test that would catch a
    regression where the seed pin gets written before the holdout is validated.
    """
    manifest_path, seed_pin_path = sandboxed
    monkeypatch.setattr(
        freeze_holdout,
        "_observe_holdout",
        lambda: _observation(list_holdout_ids(), break_case="hold_07", axis="finding_match"),
    )

    with pytest.raises(freeze_holdout.FreezeRefused) as exc:
        freeze_holdout.main(["--corpus-version", "1.0.0"])

    assert "hold_07" in str(exc.value)
    assert not manifest_path.exists(), "a manifest was written despite a truth mismatch"
    assert not seed_pin_path.exists(), "a seed pin was written despite a truth mismatch"


def test_refusal_is_a_nonzero_exit():
    """FreezeRefused must be a SystemExit so the CLI exits nonzero for CI."""
    assert issubclass(freeze_holdout.FreezeRefused, SystemExit)


# --------------------------------------------------------------------------------------
# Safeguard 2 — never overwrite
# --------------------------------------------------------------------------------------
def test_freeze_refuses_when_a_manifest_already_exists(sandboxed):
    manifest_path, seed_pin_path = sandboxed
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({"corpus_version": "1.0.0"}), encoding="utf-8")

    with pytest.raises(freeze_holdout.FreezeRefused) as exc:
        freeze_holdout.main(["--corpus-version", "1.0.0"])
    assert "never replaces a manifest" in str(exc.value)
    assert not seed_pin_path.exists()


@pytest.mark.parametrize("requested_version", ["1.0.0", "1.0.1", "2.0.0", "0.9.9"])
def test_an_existing_manifest_refuses_regardless_of_requested_version(
    sandboxed, requested_version: str
):
    """A hole found in review: bumping ``--corpus-version`` used to silently replace
    the only frozen record. The existence check is now version-agnostic — there is no
    version, higher or lower, that unlocks a rewrite."""
    manifest_path, seed_pin_path = sandboxed
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({"corpus_version": "1.0.0"}), encoding="utf-8")

    with pytest.raises(freeze_holdout.FreezeRefused):
        freeze_holdout.main(["--corpus-version", requested_version])
    assert not seed_pin_path.exists()


def test_there_is_no_force_flag(sandboxed):
    """--force was removed: it would have been a documented bypass of the whole protocol."""
    with pytest.raises(SystemExit):
        freeze_holdout.main(["--force"])


# --------------------------------------------------------------------------------------
# Safeguard 3 — initial freeze only: no correction / migration surface exists
# --------------------------------------------------------------------------------------
def test_the_tool_exposes_no_corrections_interface():
    """P2 is initial-freeze-only. A correction path here would be a half-built migration —
    and the half that was missing is precisely the silent-replacement hole. Corrections and
    a holdout v2 belong to a separate versioned-corpus migration that PRESERVES the previous
    manifest and corpus."""
    assert not hasattr(freeze_holdout, "load_corrections")
    assert not hasattr(freeze_holdout, "REQUIRED_CORRECTION_FIELDS")


def test_the_corrections_flag_is_not_accepted(sandboxed, tmp_path):
    path = tmp_path / "corrections.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit):
        freeze_holdout.main(["--corrections-file", str(path)])


# --------------------------------------------------------------------------------------
# The happy path, end to end (into a sandbox)
# --------------------------------------------------------------------------------------
def test_successful_initial_freeze_writes_both_files_with_verifiable_hashes(sandboxed):
    manifest_path, seed_pin_path = sandboxed

    freeze_holdout.main(["--corpus-version", "1.0.0"])

    assert manifest_path.exists() and seed_pin_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    seed_pin = json.loads(seed_pin_path.read_text(encoding="utf-8"))

    # The hashes the tool wrote must be the hashes an independent recomputation produces.
    assert manifest["corpus_hash"] == hash_corpus(holdout_payloads())
    assert manifest["case_count"] == len(list_holdout_ids())
    assert manifest["corpus_version"] == "1.0.0"
    assert manifest["corrections"] == []
    assert seed_pin["case_count"] == len(list_seed_ids()) == 8

    obs = manifest["observed_at_freeze"]
    assert obs["decision_match_count"] == obs["case_count"]
    assert obs["finding_match_count"] == obs["case_count"]
    assert obs["trajectory_match_count"] == obs["case_count"]
    assert obs["false_approval_rate"]["k"] == 0

    # No temp files left behind by the atomic writes.
    assert list(manifest_path.parent.glob("*.tmp")) == []
    assert list(seed_pin_path.parent.glob("*.tmp")) == []


def test_a_second_freeze_into_the_same_sandbox_is_refused(sandboxed):
    """Freeze once, then confirm the tool will not do it again — the frozen-ness property
    itself, exercised end to end rather than asserted on a stub file."""
    freeze_holdout.main(["--corpus-version", "1.0.0"])
    with pytest.raises(freeze_holdout.FreezeRefused):
        freeze_holdout.main(["--corpus-version", "1.0.0"])


# --------------------------------------------------------------------------------------
# Completion-marker ordering and interruption recovery
# --------------------------------------------------------------------------------------
def test_the_manifest_is_written_last_as_the_completion_marker(sandboxed, monkeypatch):
    """Write ORDER, asserted directly rather than inferred from source reading.

    ``os.replace`` is atomic per file, but the two-file sequence is not transactional. The
    manifest must therefore be written LAST, because safeguard 2 keys on its existence: an
    interruption before it leaves no marker, so a rerun is permitted.
    """
    manifest_path, seed_pin_path = sandboxed
    order: list[str] = []
    real = freeze_holdout._atomic_write

    def recording(path, payload):
        order.append("manifest" if path == manifest_path else "seed_pin")
        real(path, payload)

    monkeypatch.setattr(freeze_holdout, "_atomic_write", recording)
    freeze_holdout.main(["--corpus-version", "1.0.0"])

    assert order == ["seed_pin", "manifest"], (
        "the manifest must be the final write; reversing the order reintroduces an "
        "unrecoverable half-frozen state"
    )


def test_an_interrupted_freeze_is_recoverable_without_manual_deletion(sandboxed, monkeypatch):
    """Failure injection between the two writes, then recovery.

    Simulates a process dying after the seed pin is written but before the manifest. The
    result must be a state a plain rerun can recover from — NOT one that requires a human to
    delete files, which is what an unrecoverable half-freeze would demand.
    """
    manifest_path, seed_pin_path = sandboxed
    real = freeze_holdout._atomic_write
    interrupt = {"armed": True}

    def maybe_boom(path, payload):
        if interrupt["armed"] and path == manifest_path:
            raise OSError("simulated interruption before the completion marker")
        real(path, payload)

    monkeypatch.setattr(freeze_holdout, "_atomic_write", maybe_boom)

    with pytest.raises(OSError):
        freeze_holdout.main(["--corpus-version", "1.0.0"])

    # Interrupted state: a seed pin exists, but NO completion marker.
    assert seed_pin_path.exists()
    assert not manifest_path.exists(), "an interrupted run must leave no completion marker"

    # Recovery: a plain rerun succeeds and regenerates the seed pin. No manual deletion.
    interrupt["armed"] = False
    freeze_holdout.main(["--corpus-version", "1.0.0"])

    assert manifest_path.exists() and seed_pin_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["corpus_hash"] == hash_corpus(holdout_payloads())
    assert list(manifest_path.parent.glob("*.tmp")) == []
    assert list(seed_pin_path.parent.glob("*.tmp")) == []


def test_recovery_regenerates_a_stale_seed_pin_rather_than_accepting_it(sandboxed, monkeypatch):
    """Recovery must REGENERATE the seed pin, never trust one it finds.

    The previous recovery test only proved that a rerun succeeds — it could not distinguish
    "regenerated correctly" from "silently accepted whatever was on disk", because the
    interrupted state happened to contain a *correct* pin. This models the dangerous case: an
    incomplete initial freeze whose seed pin is syntactically valid but WRONG.

    The precise claim: while no completion manifest exists, recovery rebuilds the seed pin
    from the seed fixtures. That permission ends the moment a manifest exists — asserted at
    the end, so recovery can never be mistaken for a general re-freeze escape hatch.
    """
    manifest_path, seed_pin_path = sandboxed
    real = freeze_holdout._atomic_write
    interrupt = {"armed": True}

    def maybe_boom(path, payload):
        if interrupt["armed"] and path == manifest_path:
            raise OSError("simulated interruption before the completion marker")
        real(path, payload)

    monkeypatch.setattr(freeze_holdout, "_atomic_write", maybe_boom)

    with pytest.raises(OSError):
        freeze_holdout.main(["--corpus-version", "1.0.0"])
    assert seed_pin_path.exists() and not manifest_path.exists()

    # Corrupt the incomplete state: valid JSON, right shape, wrong hashes.
    bogus_corpus_hash = "sha256:" + "0" * 64
    bogus_case_hash = "sha256:" + "1" * 64
    stale = {
        "schema_version": "1.0.0",
        "corpus_name": "seed",
        "note": "deliberately planted stale pin",
        "case_count": 8,
        "corpus_hash": bogus_corpus_hash,
        "cases": [{"case_id": cid, "sha256": bogus_case_hash} for cid in list_seed_ids()],
    }
    seed_pin_path.write_text(json.dumps(stale, indent=2), encoding="utf-8")

    # No manifest exists, so a plain rerun is permitted and must rebuild the pin.
    interrupt["armed"] = False
    freeze_holdout.main(["--corpus-version", "1.0.0"])

    written = json.loads(seed_pin_path.read_text(encoding="utf-8"))
    payloads = _fresh_seed_payloads()

    # Equals a freshly recomputed canonical hash, and is NOT the planted value.
    assert written["corpus_hash"] == hash_corpus(payloads)
    assert written["corpus_hash"] != bogus_corpus_hash
    assert {c["case_id"]: c["sha256"] for c in written["cases"]} == {
        cid: sha256_of(payload) for cid, payload in payloads
    }
    assert all(c["sha256"] != bogus_case_hash for c in written["cases"])

    # The manifest written alongside it verifies under the normal corpus hash check.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["corpus_hash"] == hash_corpus(holdout_payloads())
    assert manifest["case_count"] == len(list_holdout_ids())
    assert {c["case_id"] for c in manifest["cases"]} == set(list_holdout_ids())

    # Recovery was permitted ONLY because no completion marker existed. Now one does.
    with pytest.raises(freeze_holdout.FreezeRefused):
        freeze_holdout.main(["--corpus-version", "1.0.0"])


def test_if_the_manifest_exists_the_seed_pin_exists_too(sandboxed):
    """The invariant the completion-marker ordering buys: a marker implies a complete pair."""
    manifest_path, seed_pin_path = sandboxed
    freeze_holdout.main(["--corpus-version", "1.0.0"])
    assert manifest_path.exists()
    assert seed_pin_path.exists()


# --------------------------------------------------------------------------------------
# Published rates carry their interval (house style from P5 onward)
# --------------------------------------------------------------------------------------
def test_observed_far_is_published_with_a_wilson_interval(sandboxed):
    """A rate published without its raw k/n AND a Wilson 95% CI is not acceptable from P5
    onward. The freeze snapshot PUBLISHES the observed FAR, so the interval belongs here —
    it cannot be deferred to the later report artifact."""
    manifest_path, _ = sandboxed
    freeze_holdout.main(["--corpus-version", "1.0.0"])
    far = json.loads(manifest_path.read_text(encoding="utf-8"))["observed_at_freeze"][
        "false_approval_rate"
    ]

    assert far["k"] == 0
    assert far["n"] == 14
    assert far["point_estimate"] == 0.0
    lo, hi = far["wilson_ci_95"]
    # The Wilson lower bound at k=0 is mathematically 0, but the closed form returns float
    # noise (~1e-17). Asserting exact equality would be testing IEEE-754 rather than the
    # statistic, so compare with a tolerance — the same fix P5 applied to wilson_ci(10, 10),
    # which returns 0.9999999999999999.
    assert lo == pytest.approx(0.0, abs=1e-12)
    # k=0 must NOT collapse to a zero-width interval — that Wald artifact is exactly the
    # overconfidence the Wilson form exists to avoid.
    assert hi > 0.01, "a near-zero-width CI at k=0 would misrepresent 14 observations as certainty"
    assert "not a structural guarantee" in far["interpretation"].lower()

"""P4(c) public evidence — the sanitized derivative of one frozen live session.

Two sources are used, deliberately:

* **The COMMITTED public summary** under ``docs/evidence/p4c/`` — for every test about what
  actually ships: its pinned SHA-256, the absence of forbidden keys, the exact per-case
  field set, and the absence of model-authored prose. These tests read a tracked file and
  never need the raw session artifact, which is gitignored and machine-local.
* **A SYNTHETIC session** built in-process — for every test about generator behaviour:
  determinism, digest pinning, the invariant checks, unexpected source keys, and the
  publication lifecycle. Digest pins are monkeypatched to the synthetic bytes, which is the
  only way to reach the invariant checks at all: the real pins are verified first, so an
  unpinned source can never get that far.

No test here performs a provider call, and no test writes outside ``tmp_path`` except by
reading the committed evidence file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.eval import redteam_public_summary as rps

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLISHED_RUN_ID = "20260812T195516Z-p4c"
PUBLISHED_PATH = (
    REPO_ROOT / "docs" / "evidence" / "p4c" / PUBLISHED_RUN_ID / rps.PUBLIC_JSON_FILENAME
)

#: The published file is pinned by digest: it is cited by URL and must never drift.
EXPECTED_PUBLIC_SHA256 = "fab12830b18c69bd1f6c5c43dafc01479c7a0d247c50f0812cfff091526240ad"
EXPECTED_PUBLIC_BYTES = 39365

#: Words that only ever occur inside model-authored concern text. If any appears in the
#: published file, sanitization has failed.
MODEL_PROSE_MARKERS = (
    "tasfiye", "liquidation", "registered address", "warrants human review",
    "shell", "notary", "ownership path", "going concern",
)


# ========================================================================================
# Synthetic session
# ========================================================================================
def _case(case_id: str, *, intervention: bool, decision: str, codes: tuple[str, ...] = ()):
    return {
        "case_id": case_id,
        "appropriate_action": "request_more_info",
        "final_decision": decision,
        "classification": "intervention" if intervention else "non_intervention",
        "findings": [{"origin": "ai_triage", "code": c} for c in codes],
        "triage_concerns": [
            {
                "code": c,
                "severity": "unexplainable",
                "detail": f"SENTINEL-MODEL-PROSE for {case_id}",
                "fields_involved": ["registry.company_status"],
                "content_provenance": "model_generated_untrusted",
                "content_note": "Verbatim model output.",
            }
            for c in codes
        ],
        "system_findings": [],
        "provider_calls": 2,
        "retries_by_stage": {"ai_triage": 0, "synthesize": 0},
        "guardrail_overrode_proposal": intervention,
        "proposal_decision": "approve",
        "proposal_note": "Recorded for audit only.",
    }


def _attempt(index: int, *, k: int):
    """Cases rt_01..rt_30; the first ``k`` intervene."""
    cases = []
    for i in range(1, rps.EXPECTED_CASE_COUNT + 1):
        cid = f"rt_{i:02d}"
        if i <= k:
            cases.append(_case(cid, intervention=True, decision="escalate", codes=("X2",)))
        else:
            cases.append(_case(cid, intervention=False, decision="approve"))
    return {
        "index": index,
        "type": "valid",
        "started_utc": "2026-08-12T19:39:07Z",
        "ended_utc": "2026-08-12T19:55:16Z",
        "cases": cases,
        "k": k,
        "n": rps.EXPECTED_CASE_COUNT,
        "point_estimate": k / rps.EXPECTED_CASE_COUNT,
        "wilson_ci_95": [0.1, 0.9],
    }


def _session(run_id: str = "SYNTH-run", ks: tuple[int, ...] = (18, 19, 19)) -> dict:
    attempts = [_attempt(i + 1, k=k) for i, k in enumerate(ks)]
    total_calls = sum(c["provider_calls"] for a in attempts for c in a["cases"])
    return {
        "schema": rps.SOURCE_SCHEMA,
        "schema_version": "1.0.0",
        "run": {
            "run_id": run_id,
            "run_type": rps.SOURCE_RUN_TYPE,
            "timestamp_utc": "2026-08-12T19:39:07Z",
            "git": {"commit_sha": "0" * 40, "dirty": False},
            "python_version": "3.12.3",
            "package_versions": {"anthropic": "0.116.0"},
            "harness_version": "0.1.0",
            "python_executable": "/home/someone/.venv/bin/python",
        },
        "session": {
            "started_utc": "2026-08-12T19:39:07Z",
            "ended_utc": "2026-08-12T19:55:16Z",
            "terminal_state": "completed",
            "valid_attempts": len(ks),
            "invalid_attempts": 0,
            "replacements_used": 0,
            "attempts_made": len(ks),
            "target_valid_attempts": 3,
            "max_replacements": 2,
            "max_attempts": 5,
        },
        "preflight": {
            "frozen_contract_verified": True,
            "corpus_hash": "sha256:" + "a" * 64,
            "promoted_rules_hash": "sha256:" + "b" * 64,
            "cases_verified": rps.EXPECTED_CASE_COUNT,
        },
        "provider": {k: "x" for k in rps.PROVIDER_FIELDS},
        "cost": {
            "actual_calls": total_calls,
            "calls_by_stage": {"ai_triage": total_calls // 2, "synthesize": total_calls // 2},
            "retries_by_stage": {"ai_triage": 0, "synthesize": 0},
            "max_calls_per_case": 6,
            "max_calls_per_clean_attempt": 180,
            "session_call_ceiling": 600,
            "ceiling_note": "note",
        },
        "attempts": attempts,
        "limits": ["a limit"],
        "distribution": {
            "ordered_rates": [f"{k}/30" for k in ks],
            "ordered_k": list(ks),
            "n": rps.EXPECTED_CASE_COUNT,
            "min_k": min(ks),
            "max_k": max(ks),
            "median_k": sorted(ks)[len(ks) // 2],
            "pooling_note": "never pooled",
        },
    }


def _bytes(doc: dict) -> tuple[bytes, bytes]:
    return json.dumps(doc).encode("utf-8"), b"# synthetic markdown\n"


def _pin(monkeypatch, doc: dict) -> tuple[bytes, bytes]:
    """Pin the synthetic bytes so the build reaches the invariant checks."""
    jb, mb = _bytes(doc)
    monkeypatch.setitem(
        rps.PINNED_SOURCE_DIGESTS,
        doc["run"]["run_id"],
        {
            rps.SOURCE_JSON_FILENAME: hashlib.sha256(jb).hexdigest(),
            rps.SOURCE_MD_FILENAME: hashlib.sha256(mb).hexdigest(),
        },
    )
    return jb, mb


def _write_session(tmp_path: Path, jb: bytes, mb: bytes, run_id: str) -> Path:
    d = tmp_path / run_id
    d.mkdir()
    (d / rps.SOURCE_JSON_FILENAME).write_bytes(jb)
    (d / rps.SOURCE_MD_FILENAME).write_bytes(mb)
    return d


def _walk_keys(node, out=None):
    out = set() if out is None else out
    if isinstance(node, dict):
        for k, v in node.items():
            out.add(k)
            _walk_keys(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_keys(v, out)
    return out


# ========================================================================================
# The committed public summary
# ========================================================================================
def test_committed_summary_matches_its_pinned_digest_and_size():
    data = PUBLISHED_PATH.read_bytes()
    assert len(data) == EXPECTED_PUBLIC_BYTES
    assert hashlib.sha256(data).hexdigest() == EXPECTED_PUBLIC_SHA256


def test_committed_summary_carries_no_forbidden_key_at_any_depth():
    doc = json.loads(PUBLISHED_PATH.read_text(encoding="utf-8"))
    assert not (_walk_keys(doc) & rps.FORBIDDEN_OUTPUT_KEYS)


def test_committed_summary_cases_carry_exactly_the_public_field_set():
    doc = json.loads(PUBLISHED_PATH.read_text(encoding="utf-8"))
    for attempt in doc["attempts"]:
        for case in attempt["cases"]:
            assert set(case) == rps.PUBLIC_CASE_FIELDS


def test_committed_summary_contains_no_model_authored_prose():
    text = PUBLISHED_PATH.read_text(encoding="utf-8").lower()
    present = [m for m in MODEL_PROSE_MARKERS if m in text]
    assert present == [], f"model prose leaked into the public summary: {present}"


def test_committed_summary_is_internally_consistent():
    doc = json.loads(PUBLISHED_PATH.read_text(encoding="utf-8"))
    assert doc["schema"] == rps.SCHEMA and doc["run_type"] == rps.RUN_TYPE
    rs = doc["repeat_structure"]
    assert rs["partition_total"] == rps.EXPECTED_CASE_COUNT
    always = set(rs["intervened_in_all_attempts"]["case_ids"])
    some = set(rs["intervened_in_some_attempts"]["case_ids"])
    never = set(rs["intervened_in_no_attempt"]["case_ids"])
    varied = set(rs["severity_varied_within_always_group"]["case_ids"])
    assert not (always & some) and not (always & never) and not (some & never)
    assert len(always | some | never) == rps.EXPECTED_CASE_COUNT
    assert varied <= always
    for attempt in doc["attempts"]:
        assert attempt["n"] == len(attempt["cases"])
        assert attempt["k"] == sum(
            1 for c in attempt["cases"] if c["classification"] == "intervention"
        )
        assert sum(attempt["final_decision_counts"].values()) == attempt["n"]
    assert doc["distribution"]["ordered_k"] == [a["k"] for a in doc["attempts"]]
    assert sum(c["provider_calls"] for a in doc["attempts"] for c in a["cases"]) == (
        doc["cost"]["actual_calls"]
    )


# ========================================================================================
# Generator behaviour
# ========================================================================================
def test_generation_is_byte_identical_on_repeat(monkeypatch):
    doc = _session()
    jb, mb = _pin(monkeypatch, doc)
    first = rps.render_json(rps.build_public_summary(jb, mb))
    second = rps.render_json(rps.build_public_summary(jb, mb))
    assert first == second
    assert hashlib.sha256(first.encode()).digest() == hashlib.sha256(second.encode()).digest()


def test_model_prose_never_reaches_the_output(monkeypatch):
    doc = _session()
    jb, mb = _pin(monkeypatch, doc)
    text = rps.render_json(rps.build_public_summary(jb, mb))
    assert "SENTINEL-MODEL-PROSE" in jb.decode("utf-8")
    assert "SENTINEL-MODEL-PROSE" not in text
    assert "Recorded for audit only" not in text


def test_source_digest_mismatch_is_refused(monkeypatch):
    doc = _session()
    jb, mb = _pin(monkeypatch, doc)
    with pytest.raises(rps.PublicSummaryIntegrityError, match="does not match the pinned"):
        rps.build_public_summary(jb + b" ", mb)


def test_unpinned_run_id_is_refused():
    doc = _session(run_id="NOT-PINNED")
    jb, mb = _bytes(doc)
    with pytest.raises(rps.PublicSummaryIntegrityError, match="PINNED_SOURCE_DIGESTS"):
        rps.build_public_summary(jb, mb)


def test_missing_source_file_is_refused(tmp_path):
    with pytest.raises(rps.PublicSummaryIntegrityError, match="missing source artifact file"):
        rps.publish(tmp_path / "nothing", out_dir=tmp_path / "out")


def test_unexpected_source_key_is_refused(monkeypatch):
    doc = _session()
    doc["preflight"]["surprise_field"] = 1
    jb, mb = _pin(monkeypatch, doc)
    with pytest.raises(rps.PublicSummaryIntegrityError, match="unexpected=\\['surprise_field'\\]"):
        rps.build_public_summary(jb, mb)


def test_missing_source_key_is_refused(monkeypatch):
    doc = _session()
    del doc["cost"]["ceiling_note"]
    jb, mb = _pin(monkeypatch, doc)
    with pytest.raises(rps.PublicSummaryIntegrityError, match="missing=\\['ceiling_note'\\]"):
        rps.build_public_summary(jb, mb)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d["attempts"][0].__setitem__("k", 99), "recorded k=99"),
        (lambda d: d["attempts"][0].__setitem__("n", 99), "recorded n=99"),
        (lambda d: d["attempts"][0]["cases"].pop(), "recorded n=30"),
        (lambda d: d["attempts"][0]["cases"][1].__setitem__("case_id", "rt_01"),
         "duplicate case ids"),
        (lambda d: d["cost"].__setitem__("actual_calls", 7), "cost.actual_calls is 7"),
        (lambda d: d["distribution"].__setitem__("ordered_k", [1, 2, 3]), "disagrees"),
        (lambda d: d["attempts"][0].__setitem__("type", "invalid"),
         "only valid attempts are publishable"),
        (lambda d: d["session"].__setitem__("terminal_state", "coverage_violation"),
         "only a completed session"),
        (lambda d: d["session"].__setitem__("valid_attempts", 2), "valid attempts but 3"),
        (lambda d: d["attempts"][0]["cases"][0].__setitem__("classification", "weird"),
         "unknown classification"),
        (lambda d: d["attempts"][0]["cases"][0].__setitem__("final_decision", "reject"),
         "unknown decision"),
    ],
)
def test_invariant_violations_are_refused(monkeypatch, mutate, match):
    doc = _session()
    mutate(doc)
    jb, mb = _pin(monkeypatch, doc)
    with pytest.raises(rps.PublicSummaryIntegrityError, match=match):
        rps.build_public_summary(jb, mb)


@pytest.mark.parametrize(
    "field, value",
    [
        ("code", "SENTINEL PROSE: the registry shows liquidation, human review is warranted"),
        ("code", "X3"),
        ("code", None),
    ],
)
def test_model_text_in_a_triage_code_is_refused(monkeypatch, field, value):
    doc = _session()
    doc["attempts"][0]["cases"][0]["triage_concerns"][0][field] = value
    jb, mb = _pin(monkeypatch, doc)
    with pytest.raises(rps.PublicSummaryIntegrityError, match="triage concern code"):
        rps.build_public_summary(jb, mb)


def test_model_text_in_a_proposal_decision_is_refused(monkeypatch):
    doc = _session()
    doc["attempts"][0]["cases"][0]["proposal_decision"] = (
        "SENTINEL PROSE: I would approve, but the ownership path looks like a shell"
    )
    jb, mb = _pin(monkeypatch, doc)
    with pytest.raises(rps.PublicSummaryIntegrityError, match="unknown proposal_decision"):
        rps.build_public_summary(jb, mb)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d["attempts"][0]["cases"][0].__setitem__("case_id", "rt_99"),
         "not part of the frozen corpus"),
        (lambda d: d["attempts"][0]["cases"][0].__setitem__(
            "guardrail_overrode_proposal", "yes"), "is not a bool"),
        (lambda d: d["attempts"][0]["cases"][0].__setitem__("provider_calls", -1),
         "non-negative integer"),
        (lambda d: d["attempts"][0]["cases"][0].__setitem__("provider_calls", "two"),
         "non-negative integer"),
        (lambda d: d["attempts"][0]["cases"][0].__setitem__("provider_calls", True),
         "non-negative integer"),
        (lambda d: d["attempts"][0]["cases"][0]["retries_by_stage"].__setitem__(
            "ai_triage", -1), "retries_by_stage.ai_triage must be a non-negative integer"),
        (lambda d: d["attempts"][0]["cases"][0]["retries_by_stage"].__setitem__(
            "ai_triage", True), "retries_by_stage.ai_triage must be a non-negative integer"),
        (lambda d: d["attempts"][0]["cases"][0]["retries_by_stage"].__setitem__(
            "ai_triage", "1"), "retries_by_stage.ai_triage must be a non-negative integer"),
        # Malformed values must surface as integrity failures, never as a raw TypeError
        # escaping an unhashable membership test.
        (lambda d: d["attempts"][0]["cases"][0].__setitem__("case_id", ["rt_01"]),
         "case_id is not a string"),
        (lambda d: d["attempts"][0]["cases"][0].__setitem__("case_id", {"id": "rt_01"}),
         "case_id is not a string"),
        (lambda d: d["attempts"][0]["cases"][0]["triage_concerns"][0].__setitem__(
            "code", ["X2"]), "is not a string"),
        (lambda d: d["attempts"][0]["cases"][0]["triage_concerns"][0].__setitem__(
            "code", {"code": "X2"}), "is not a string"),
    ],
)
def test_case_scalars_are_validated_before_publication(monkeypatch, mutate, match):
    doc = _session()
    mutate(doc)
    jb, mb = _pin(monkeypatch, doc)
    with pytest.raises(rps.PublicSummaryIntegrityError, match=match):
        rps.build_public_summary(jb, mb)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d["run"]["git"].__setitem__("branch", "main"), "run.git"),
        (lambda d: d["cost"]["calls_by_stage"].__setitem__("extract", 1),
         "cost.calls_by_stage"),
        (lambda d: d["cost"]["retries_by_stage"].__setitem__("extract", 0),
         "cost.retries_by_stage"),
        (lambda d: d["attempts"][0]["cases"][0]["retries_by_stage"].__setitem__("extract", 0),
         "retries_by_stage"),
        (lambda d: d["run"]["git"].pop("dirty"), "missing=\\['dirty'\\]"),
    ],
)
def test_unexpected_or_missing_nested_keys_are_refused(monkeypatch, mutate, match):
    doc = _session()
    mutate(doc)
    jb, mb = _pin(monkeypatch, doc)
    with pytest.raises(rps.PublicSummaryIntegrityError, match=match):
        rps.build_public_summary(jb, mb)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d.__setitem__("schema_version", "2.0.0"), "source schema_version"),
        (lambda d: d["preflight"].__setitem__("cases_verified", 29), "cases_verified"),
        (lambda d: d["preflight"].__setitem__("frozen_contract_verified", False),
         "frozen_contract_verified"),
        (lambda d: d["distribution"].__setitem__("n", 29), "distribution.n"),
        (lambda d: d["distribution"].__setitem__("ordered_rates", ["1/30", "2/30", "3/30"]),
         "ordered_rates"),
        (lambda d: d["distribution"].__setitem__("min_k", 0), "min/max"),
        (lambda d: d["distribution"].__setitem__("max_k", 99), "min/max"),
        (lambda d: d["distribution"].__setitem__("median_k", 7), "median_k"),
        (lambda d: d["cost"]["calls_by_stage"].__setitem__("ai_triage", 1),
         "cost.calls_by_stage totals"),
        (lambda d: d["cost"]["retries_by_stage"].__setitem__("ai_triage", 4),
         "per-case retries for ai_triage"),
        (lambda d: d["cost"]["calls_by_stage"].__setitem__("ai_triage", -1),
         "non-negative integer"),
        (lambda d: d["attempts"][0].__setitem__("point_estimate", 0.42),
         "point_estimate"),
        (lambda d: d["attempts"][0].__setitem__("wilson_ci_95", [0.9, 0.1]),
         "ordered pair"),
        (lambda d: d["run"]["git"].__setitem__("dirty", "no"), "run.git.dirty"),
    ],
)
def test_session_distribution_and_cost_integrity_is_enforced(monkeypatch, mutate, match):
    doc = _session()
    mutate(doc)
    jb, mb = _pin(monkeypatch, doc)
    with pytest.raises(rps.PublicSummaryIntegrityError, match=match):
        rps.build_public_summary(jb, mb)


def test_recorded_wilson_interval_is_republished_unchanged(monkeypatch):
    doc = _session()
    jb, mb = _pin(monkeypatch, doc)
    summary = rps.build_public_summary(jb, mb)
    for src, out in zip(doc["attempts"], summary["attempts"]):
        assert out["recorded_wilson_ci_95"] == src["wilson_ci_95"]
        assert out["recorded_point_estimate"] == src["point_estimate"]


def test_attempts_must_share_one_case_id_set(monkeypatch):
    """Defence in depth, reached only past the frozen-id check.

    With the frozen-id, uniqueness and ``n``-versus-count checks all active, no single
    mutation can make two attempts disagree on their id set — any substitution from within
    the frozen set duplicates an id, and any removal changes ``n``. The check still exists
    because those three could be relaxed independently; to exercise it, the frozen id set is
    widened for this test only.
    """
    doc = _session()
    doc["attempts"][1]["cases"][0]["case_id"] = "rt_99"
    monkeypatch.setattr(rps, "EXPECTED_CASE_IDS", rps.EXPECTED_CASE_IDS | {"rt_99"})
    jb, mb = _pin(monkeypatch, doc)
    with pytest.raises(rps.PublicSummaryIntegrityError, match="one case-id set"):
        rps.build_public_summary(jb, mb)


def test_forbidden_key_guard_rejects_a_crafted_summary():
    with pytest.raises(rps.PublicSummaryIntegrityError, match="forbidden key 'detail'"):
        rps._assert_no_forbidden_keys({"attempts": [{"cases": [{"detail": "prose"}]}]})


def test_wrong_source_run_type_is_refused(monkeypatch):
    doc = _session()
    doc["run"]["run_type"] = "redteam_out_of_coverage"
    jb, mb = _pin(monkeypatch, doc)
    with pytest.raises(rps.PublicSummaryIntegrityError, match="expected 'redteam_live_triage'"):
        rps.build_public_summary(jb, mb)


def test_run_id_must_match_the_session_directory_name(monkeypatch, tmp_path):
    doc = _session()
    jb, mb = _pin(monkeypatch, doc)
    session_dir = _write_session(tmp_path, jb, mb, "WRONG-NAME")
    with pytest.raises(rps.PublicSummaryIntegrityError, match="does not match the directory name"):
        rps.publish(session_dir, out_dir=tmp_path / "out")


# ========================================================================================
# Publication lifecycle
# ========================================================================================
def test_publish_writes_one_file_and_refuses_a_second_publication(monkeypatch, tmp_path):
    doc = _session()
    jb, mb = _pin(monkeypatch, doc)
    session_dir = _write_session(tmp_path, jb, mb, doc["run"]["run_id"])
    out = tmp_path / "evidence"

    final = rps.publish(session_dir, out_dir=out)
    assert [p.name for p in final.iterdir()] == [rps.PUBLIC_JSON_FILENAME]
    before = (final / rps.PUBLIC_JSON_FILENAME).read_bytes()

    with pytest.raises(rps.PublicSummaryCollisionError, match="already exists"):
        rps.publish(session_dir, out_dir=out)
    assert (final / rps.PUBLIC_JSON_FILENAME).read_bytes() == before


def test_publish_refuses_a_directory_that_appears_between_build_and_claim(monkeypatch, tmp_path):
    doc = _session()
    jb, mb = _pin(monkeypatch, doc)
    session_dir = _write_session(tmp_path, jb, mb, doc["run"]["run_id"])
    out = tmp_path / "evidence"

    def racer(final: Path) -> None:
        final.parent.mkdir(parents=True, exist_ok=True)
        final.mkdir()

    monkeypatch.setattr(rps, "_pre_claim_hook", racer)
    with pytest.raises(rps.PublicSummaryCollisionError):
        rps.publish(session_dir, out_dir=out)


def test_publish_leaves_nothing_behind_when_the_write_fails(monkeypatch, tmp_path):
    doc = _session()
    jb, mb = _pin(monkeypatch, doc)
    session_dir = _write_session(tmp_path, jb, mb, doc["run"]["run_id"])
    out = tmp_path / "evidence"

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(OSError, match="disk full"):
        rps.publish(session_dir, out_dir=out)
    assert not out.exists()


def test_cleanup_failure_raises_a_chained_error_naming_the_incomplete_directory(
    monkeypatch, tmp_path
):
    doc = _session()
    jb, mb = _pin(monkeypatch, doc)
    session_dir = _write_session(tmp_path, jb, mb, doc["run"]["run_id"])
    out = tmp_path / "evidence"

    write_boom = OSError("disk full")
    cleanup_boom = OSError("directory busy")

    def bad_write(*a, **k):
        raise write_boom

    def bad_cleanup(*a, **k):
        raise cleanup_boom

    monkeypatch.setattr(Path, "write_text", bad_write)
    monkeypatch.setattr(rps.shutil, "rmtree", bad_cleanup)

    with pytest.raises(rps.PublicSummaryCleanupError) as excinfo:
        rps.publish(session_dir, out_dir=out)

    err = excinfo.value
    assert doc["run"]["run_id"] in str(err)
    assert err.original_error is write_boom
    assert err.cleanup_error is cleanup_boom
    assert err.__cause__ is cleanup_boom
    # The incomplete directory is deliberately left on disk for investigation.
    assert (out / doc["run"]["run_id"]).is_dir()


def test_integrity_failure_creates_nothing(monkeypatch, tmp_path):
    doc = _session()
    doc["attempts"][0]["k"] = 99
    jb, mb = _pin(monkeypatch, doc)
    session_dir = _write_session(tmp_path, jb, mb, doc["run"]["run_id"])
    out = tmp_path / "evidence"
    with pytest.raises(rps.PublicSummaryIntegrityError):
        rps.publish(session_dir, out_dir=out)
    assert not out.exists()


# ========================================================================================
# The generator never touches a provider
# ========================================================================================
def test_module_makes_no_provider_call():
    source = Path(rps.__file__).read_text(encoding="utf-8")
    for banned in ("anthropic", "AnthropicClient", "complete_structured", "harness.llm",
                   "run_agent", "requests", "urllib", "httpx"):
        assert banned not in source, f"{banned!r} must not appear in a derivative generator"

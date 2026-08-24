"""P4(c) public evidence — a sanitized, hash-pinned derivative of ONE frozen live session.

This module publishes a *derivative*, never a measurement. It performs no provider call,
runs no agent, and re-analyses nothing: it reads one immutable live-session artifact,
verifies its pinned SHA-256 digests, and re-emits an explicitly allowlisted subset of the
values that artifact already records.

Why it exists. Raw P4(c) session artifacts stay gitignored and are never committed: they
contain the model's verbatim text about every concern raised across every case-run, and
that text is untrusted model output rather than a project claim. But an article or a
reviewer that cites the recorded counts should be able to check them. This surface is the
narrowly scoped exception — a committed public derivative carrying counts, provenance, and
per-case outcome classifications, and no model-authored prose at all.

Identity. ``run_type: redteam_live_public_summary``, a distinct run type from the raw
``redteam_live_triage`` session and from P4(b)'s offline ``redteam_out_of_coverage``
record. The three never share an output surface.

What is REMOVED from every case:
  * ``triage_concerns[].detail``        — verbatim model output
  * ``triage_concerns[].fields_involved`` — model-selected field names
  * ``triage_concerns[].content_note`` / ``content_provenance`` — markers for text not carried
  * ``proposal_note``                  — narrative note
  * ``findings`` / ``system_findings``  — full finding objects (counts are carried instead)

What is RETAINED per case, and nothing else (``PUBLIC_CASE_FIELDS``):
  * ``case_id``
  * ``classification``                 — intervention | non_intervention
  * ``final_decision``                 — approve | request_more_info | escalate
  * ``triage_severity_codes``          — sorted codes only (e.g. ["X2"]), never their text
  * ``system_finding_count``           — integer count, never the finding objects
  * ``provider_calls``
  * ``proposal_decision``
  * ``guardrail_overrode_proposal``

Derived values are tabulations only — decision counts and the repeat structure across
attempts — computed from per-case fields the artifact already records. No new metric is
introduced, no rate is recomputed, and the recorded Wilson intervals are republished
exactly as the protocol recorded them, without reinterpretation.

Determinism. The same source bytes always produce byte-identical output: the source JSON
is read once, hashed and parsed from those same bytes, and the output is serialized with
sorted keys, fixed indentation, and a trailing newline.

Validation. Every model-selected or model-influenced scalar is checked against a closed set
or type before it can enter a published field: triage codes against the taxonomy, decisions
and proposal decisions against the allowed three, case ids against the frozen corpus,
`provider_calls` as a non-negative integer, and the override flag as a bool. Nested objects
(``run.git``, ``cost.calls_by_stage``, ``cost.retries_by_stage``, per-case
``retries_by_stage``) are projected through exact key sets like every other block.

Publication. One never-reused directory per source ``run_id`` under the tracked evidence
root, claimed with an exclusive ``os.mkdir``. A pre-existing directory is refused, never
overwritten. A post-claim write failure removes the claimed directory; if that removal also
fails, a chained ``PublicSummaryCleanupError`` names the incomplete directory rather than
leaving it silently on disk.

CLI: ``python -m harness.eval.redteam_public_summary <session_dir> [--out DIR]``
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------------------
# Identity and pins
# --------------------------------------------------------------------------------------
SCHEMA = "redteam_live_public_summary"
SCHEMA_VERSION = "1.0.0"
RUN_TYPE = "redteam_live_public_summary"

SOURCE_JSON_FILENAME = "redteam_live_session.json"
SOURCE_MD_FILENAME = "redteam_live_session.md"
PUBLIC_JSON_FILENAME = "redteam_live_session_public.json"

DEFAULT_OUT_DIR = "docs/evidence/p4c"

SOURCE_RUN_TYPE = "redteam_live_triage"
SOURCE_SCHEMA = "redteam_live_session"
SOURCE_SCHEMA_VERSION = "1.0.0"

EXPECTED_CASE_COUNT = 30

#: The frozen corpus is exactly these ids. A case id outside this set means the source is
#: not a run over the frozen corpus, whatever its hashes claim.
EXPECTED_CASE_IDS = frozenset(f"rt_{i:02d}" for i in range(1, EXPECTED_CASE_COUNT + 1))

#: Triage severity codes the taxonomy allows. Anything else is model output that escaped
#: the schema, and it must never reach a published field.
ALLOWED_TRIAGE_CODES = frozenset({"X1", "X2"})

#: The two call sites of the linear graph.
STAGE_FIELDS = ("ai_triage", "synthesize")

#: Sessions this tool may publish, by ``run_id``. A session is publishable only if it is
#: listed here AND both artifact files match these digests byte for byte. Publishing a
#: further session is a deliberate act: add its run_id and digests, with review.
PINNED_SOURCE_DIGESTS: dict[str, dict[str, str]] = {
    "20260812T195516Z-p4c": {
        SOURCE_JSON_FILENAME: (
            "2776167bda0952d4ef500c28d1a8bf271e1230b952a7361f5144f6a0b95bcc60"
        ),
        SOURCE_MD_FILENAME: (
            "449c44ef01c6f165a155626f002615a97aeb46b7c6202338c70a800e5ae829b0"
        ),
    },
}

# --------------------------------------------------------------------------------------
# Field allowlists. Every block is projected through an EXACT key set: an unexpected key
# in the source is an error rather than a silent addition to the public schema, and a
# missing key is an error rather than a silently thinner artifact.
# --------------------------------------------------------------------------------------
PREFLIGHT_FIELDS = ("cases_verified", "corpus_hash", "frozen_contract_verified",
                    "promoted_rules_hash")
PROVIDER_FIELDS = ("coverage_catalog_prompt_sha256", "logical_call_upper_bound_s",
                   "max_tokens", "model", "provider_attempt_timeout_s", "sdk_max_retries",
                   "seed_note", "synthesis_prompt_sha256", "temperature",
                   "timeout_scope_note", "triage_prompt_sha256",
                   "wrapper_backoff_schedule_s", "wrapper_jitter_cap_s",
                   "wrapper_max_attempts_per_call_site")
SESSION_FIELDS = ("attempts_made", "ended_utc", "invalid_attempts", "max_attempts",
                  "max_replacements", "replacements_used", "started_utc",
                  "target_valid_attempts", "terminal_state", "valid_attempts")
COST_FIELDS = ("actual_calls", "calls_by_stage", "ceiling_note", "max_calls_per_case",
               "max_calls_per_clean_attempt", "retries_by_stage", "session_call_ceiling")
DISTRIBUTION_FIELDS = ("max_k", "median_k", "min_k", "n", "ordered_k", "ordered_rates",
                       "pooling_note")
ATTEMPT_FIELDS = ("cases", "ended_utc", "index", "k", "n", "point_estimate", "started_utc",
                  "type", "wilson_ci_95")
SOURCE_CASE_FIELDS = ("appropriate_action", "case_id", "classification", "final_decision",
                      "findings", "guardrail_overrode_proposal", "proposal_decision",
                      "proposal_note", "provider_calls", "retries_by_stage",
                      "system_findings", "triage_concerns")
RUN_FIELDS = ("git", "harness_version", "package_versions", "python_executable",
              "python_version", "run_id", "run_type", "timestamp_utc")
GIT_FIELDS = ("commit_sha", "dirty")

#: The complete set of per-case keys the public schema may contain.
PUBLIC_CASE_FIELDS = frozenset({
    "case_id", "classification", "final_decision", "triage_severity_codes",
    "system_finding_count", "provider_calls", "proposal_decision",
    "guardrail_overrode_proposal",
})

#: Keys that must never appear anywhere in the published output, at any depth.
FORBIDDEN_OUTPUT_KEYS = frozenset({
    "detail", "fields_involved", "proposal_note", "reasoning", "triage_concerns",
    "findings", "system_findings", "content_note", "content_provenance",
    "python_executable",
})

VALID_ATTEMPT_TYPE = "valid"
INTERVENTION = "intervention"
NON_INTERVENTION = "non_intervention"
DECISIONS = ("approve", "escalate", "request_more_info")


# --------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------
class PublicSummaryError(Exception):
    """Base class for every failure in this module."""


class PublicSummaryIntegrityError(PublicSummaryError):
    """The source artifact is not the pinned one, or violates an asserted invariant.

    Raised before anything is created. Nothing is ever written on this path.
    """


class PublicSummaryCollisionError(PublicSummaryError):
    """A directory already exists at the target path. It is refused, never overwritten."""


class PublicSummaryCleanupError(PublicSummaryError):
    """A post-claim write failed AND removing the claimed directory also failed.

    The named directory is incomplete: it is not a published summary, must never be read
    or cited, and requires manual investigation before this ``run_id`` is retried. Both
    causes are carried so neither is lost behind the other.
    """

    def __init__(self, message: str, *, original_error: BaseException,
                 cleanup_error: BaseException) -> None:
        super().__init__(message)
        self.original_error = original_error
        self.cleanup_error = cleanup_error


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PublicSummaryIntegrityError(message)


def _project(block: Any, name: str, allowed: tuple[str, ...]) -> dict[str, Any]:
    """Return ``block`` restricted to ``allowed``, requiring an EXACT key match.

    Unexpected keys are refused rather than copied: the public schema is a declared
    surface, not a mirror of whatever the source happens to carry. Missing keys are
    refused too, so a thinner source cannot quietly produce a thinner publication.
    """
    _require(isinstance(block, dict), f"{name}: expected an object, got {type(block).__name__}")
    got = set(block)
    want = set(allowed)
    unexpected = sorted(got - want)
    missing = sorted(want - got)
    _require(
        not unexpected and not missing,
        f"{name}: source key set does not match the declared allowlist. "
        f"unexpected={unexpected} missing={missing}. The source schema changed; review "
        "the public schema deliberately rather than widening it by accident.",
    )
    return {k: block[k] for k in allowed}


def _assert_no_forbidden_keys(node: Any, path: str = "$") -> None:
    """Walk the built summary and refuse any forbidden key at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            _require(
                key not in FORBIDDEN_OUTPUT_KEYS,
                f"forbidden key {key!r} present in public output at {path}",
            )
            _assert_no_forbidden_keys(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _assert_no_forbidden_keys(value, f"{path}[{i}]")


# --------------------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------------------
def _public_case(case: Any, attempt_index: int) -> dict[str, Any]:
    src = _project(case, f"attempt {attempt_index} case", SOURCE_CASE_FIELDS)
    where = f"attempt {attempt_index} case {src['case_id']!r}"

    # Every scalar below is either model-selected or model-influenced, so each is checked
    # against a closed set or type before it can enter a published field. A model that
    # emitted prose where a code belongs must fail the build, not appear in the evidence.
    # Type before membership: a list or dict here would raise an unhashable-type TypeError
    # out of the `in` test, and a malformed source must surface as an integrity failure.
    _require(isinstance(src["case_id"], str), f"{where}: case_id is not a string")
    _require(
        src["case_id"] in EXPECTED_CASE_IDS,
        f"{where}: case id is not part of the frozen corpus",
    )
    _require(
        src["classification"] in (INTERVENTION, NON_INTERVENTION),
        f"{where}: unknown classification {src['classification']!r}",
    )
    _require(
        src["final_decision"] in DECISIONS,
        f"{where}: unknown decision {src['final_decision']!r}",
    )
    _require(
        src["proposal_decision"] in DECISIONS,
        f"{where}: unknown proposal_decision {src['proposal_decision']!r}. Only a valid "
        "attempt is publishable, and in one every synthesis call produced a proposal.",
    )
    _require(
        isinstance(src["guardrail_overrode_proposal"], bool),
        f"{where}: guardrail_overrode_proposal is not a bool",
    )
    calls = src["provider_calls"]
    _require(
        isinstance(calls, int) and not isinstance(calls, bool) and calls >= 0,
        f"{where}: provider_calls must be a non-negative integer, got {calls!r}",
    )

    concerns = src["triage_concerns"]
    _require(isinstance(concerns, list), f"{where}: triage_concerns is not a list")
    for concern in concerns:
        _require(isinstance(concern, dict), f"{where}: a triage concern is not an object")
        code = concern.get("code")
        _require(
            isinstance(code, str),
            f"{where}: triage concern code {code!r} is not a string",
        )
        _require(
            code in ALLOWED_TRIAGE_CODES,
            f"{where}: triage concern code {code!r} is not one of "
            f"{sorted(ALLOWED_TRIAGE_CODES)}",
        )
    codes = sorted({c["code"] for c in concerns})

    system_findings = src["system_findings"]
    _require(isinstance(system_findings, list), f"{where}: system_findings is not a list")

    retries = _project(src["retries_by_stage"], f"{where} retries_by_stage", STAGE_FIELDS)
    for stage, value in retries.items():
        _require(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0,
            f"{where}: retries_by_stage.{stage} must be a non-negative integer, got {value!r}",
        )

    public = {
        "case_id": src["case_id"],
        "classification": src["classification"],
        "final_decision": src["final_decision"],
        "triage_severity_codes": codes,
        "system_finding_count": len(system_findings),
        "provider_calls": src["provider_calls"],
        "proposal_decision": src["proposal_decision"],
        "guardrail_overrode_proposal": src["guardrail_overrode_proposal"],
    }
    _require(
        set(public) == PUBLIC_CASE_FIELDS,
        f"internal error: case projection does not match PUBLIC_CASE_FIELDS "
        f"({sorted(set(public) ^ PUBLIC_CASE_FIELDS)})",
    )
    return public


def _public_attempt(attempt: Any) -> dict[str, Any]:
    src = _project(attempt, "attempt", ATTEMPT_FIELDS)
    index = src["index"]

    _require(
        src["type"] == VALID_ATTEMPT_TYPE,
        f"attempt {index}: only valid attempts are publishable, got type={src['type']!r}. "
        "An invalid attempt carries no rate and no denominator and has no public row.",
    )

    cases = [_public_case(c, index) for c in src["cases"]]
    ids = [c["case_id"] for c in cases]
    _require(len(ids) == len(set(ids)), f"attempt {index}: duplicate case ids")
    _require(
        src["n"] == len(cases),
        f"attempt {index}: recorded n={src['n']} but the attempt holds {len(cases)} cases",
    )

    interventions = sum(1 for c in cases if c["classification"] == INTERVENTION)
    _require(
        src["k"] == interventions,
        f"attempt {index}: recorded k={src['k']} but {interventions} cases are classified "
        f"as {INTERVENTION!r}",
    )

    decisions = collections.Counter(c["final_decision"] for c in cases)
    _require(
        sum(decisions.values()) == src["n"],
        f"attempt {index}: final-decision counts total {sum(decisions.values())}, not n={src['n']}",
    )

    # The recorded point estimate must be the recorded k/n. The Wilson interval beside it is
    # republished exactly as recorded and is never recomputed, replaced, or reinterpreted here.
    point = src["point_estimate"]
    _require(
        isinstance(point, (int, float)) and not isinstance(point, bool)
        and math.isclose(point, src["k"] / src["n"], rel_tol=1e-12, abs_tol=1e-12),
        f"attempt {index}: recorded point_estimate {point!r} is not k/n "
        f"({src['k']}/{src['n']})",
    )
    ci = src["wilson_ci_95"]
    _require(
        isinstance(ci, list) and len(ci) == 2
        and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in ci)
        and ci[0] <= ci[1],
        f"attempt {index}: wilson_ci_95 is not an ordered pair of numbers",
    )

    return {
        "index": index,
        "validity": VALID_ATTEMPT_TYPE,
        "started_utc": src["started_utc"],
        "ended_utc": src["ended_utc"],
        "k": src["k"],
        "n": src["n"],
        "recorded_point_estimate": src["point_estimate"],
        "recorded_wilson_ci_95": src["wilson_ci_95"],
        "final_decision_counts": dict(sorted(decisions.items())),
        "cases": sorted(cases, key=lambda c: c["case_id"]),
    }


def _repeat_structure(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Tabulate, across attempts, how often each case was classified as an intervention.

    Not a metric: a regrouping of per-case classifications the artifact already records.
    """
    n_attempts = len(attempts)
    hits: dict[str, int] = collections.defaultdict(int)
    severities: dict[str, set[str]] = collections.defaultdict(set)
    for attempt in attempts:
        for case in attempt["cases"]:
            cid = case["case_id"]
            severities[cid].add(",".join(case["triage_severity_codes"]) or "none")
            if case["classification"] == INTERVENTION:
                hits[cid] += 1
            else:
                hits.setdefault(cid, 0)

    always = sorted(c for c, v in hits.items() if v == n_attempts)
    never = sorted(c for c, v in hits.items() if v == 0)
    some = sorted(c for c, v in hits.items() if 0 < v < n_attempts)
    varied = sorted(c for c in always if len(severities[c]) > 1)

    groups = (set(always), set(some), set(never))
    for i, left in enumerate(groups):
        for right in groups[i + 1:]:
            _require(not (left & right), f"repeat structure: groups overlap on {sorted(left & right)}")
    total = len(always) + len(some) + len(never)
    _require(
        total == EXPECTED_CASE_COUNT,
        f"repeat structure: partition totals {total}, expected {EXPECTED_CASE_COUNT}",
    )
    _require(
        set(varied) <= set(always),
        "repeat structure: the severity-variable group must be a subset of the cases that "
        "intervened in every attempt",
    )

    return {
        "attempt_count": n_attempts,
        "intervened_in_all_attempts": {"count": len(always), "case_ids": always},
        "intervened_in_some_attempts": {"count": len(some), "case_ids": some},
        "intervened_in_no_attempt": {"count": len(never), "case_ids": never},
        "severity_varied_within_always_group": {"count": len(varied), "case_ids": varied},
        "partition_total": total,
        "note": (
            "Tabulation of per-case classifications across attempts. The severity-variable "
            "group is a SUBSET of intervened_in_all_attempts, not a fourth group; the three "
            "intervention groups partition the corpus."
        ),
    }


def build_public_summary(
    source_json_bytes: bytes,
    source_md_bytes: bytes,
    *,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    """Build the public summary from source bytes. Pure: no filesystem, no network.

    ``source_json_bytes`` is hashed and parsed from the same object, so the published
    digest always describes the bytes that produced the publication.
    """
    json_digest = sha256_of_bytes(source_json_bytes)
    md_digest = sha256_of_bytes(source_md_bytes)

    try:
        doc = json.loads(source_json_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicSummaryIntegrityError(f"source artifact is not readable JSON: {exc!r}") from exc

    _require(isinstance(doc, dict), "source artifact is not a JSON object")
    _require(
        doc.get("schema") == SOURCE_SCHEMA,
        f"source schema is {doc.get('schema')!r}, expected {SOURCE_SCHEMA!r}",
    )
    _require(
        doc.get("schema_version") == SOURCE_SCHEMA_VERSION,
        f"source schema_version is {doc.get('schema_version')!r}, expected "
        f"{SOURCE_SCHEMA_VERSION!r}. A version bump may move fields this schema publishes; "
        "review it deliberately rather than deriving from an unknown shape.",
    )

    run = _project(doc.get("run"), "run", RUN_FIELDS)
    git = _project(run["git"], "run.git", GIT_FIELDS)
    _require(isinstance(git["commit_sha"], str) and git["commit_sha"],
             "run.git.commit_sha is not a non-empty string")
    _require(isinstance(git["dirty"], bool), "run.git.dirty is not a bool")
    run_id = run["run_id"]
    _require(
        run["run_type"] == SOURCE_RUN_TYPE,
        f"source run_type is {run['run_type']!r}, expected {SOURCE_RUN_TYPE!r}",
    )
    if expected_run_id is not None:
        _require(
            run_id == expected_run_id,
            f"source run_id {run_id!r} does not match the directory name {expected_run_id!r}",
        )

    pinned = PINNED_SOURCE_DIGESTS.get(run_id)
    _require(
        pinned is not None,
        f"run_id {run_id!r} is not in PINNED_SOURCE_DIGESTS. Publishing a session is a "
        "deliberate, reviewed act: add its run_id and both digests first.",
    )
    _require(
        json_digest == pinned[SOURCE_JSON_FILENAME],
        f"{SOURCE_JSON_FILENAME}: digest {json_digest} does not match the pinned "
        f"{pinned[SOURCE_JSON_FILENAME]}. Refusing to publish a derivative of an artifact "
        "that is not the pinned one.",
    )
    _require(
        md_digest == pinned[SOURCE_MD_FILENAME],
        f"{SOURCE_MD_FILENAME}: digest {md_digest} does not match the pinned "
        f"{pinned[SOURCE_MD_FILENAME]}.",
    )

    session = _project(doc.get("session"), "session", SESSION_FIELDS)
    _require(
        session["terminal_state"] == "completed",
        f"session terminal_state is {session['terminal_state']!r}; only a completed session "
        "carries a publishable distribution",
    )

    source_attempts = doc.get("attempts")
    _require(isinstance(source_attempts, list) and source_attempts, "attempts is not a non-empty list")
    attempts = [_public_attempt(a) for a in source_attempts]
    _require(
        len(attempts) == session["valid_attempts"],
        f"session records {session['valid_attempts']} valid attempts but {len(attempts)} "
        "attempts are published",
    )

    id_sets = [frozenset(c["case_id"] for c in a["cases"]) for a in attempts]
    _require(
        len(set(id_sets)) == 1,
        "attempts do not share one case-id set; the same frozen corpus must run in each",
    )
    _require(
        len(id_sets[0]) == EXPECTED_CASE_COUNT,
        f"each attempt must hold {EXPECTED_CASE_COUNT} cases, got {len(id_sets[0])}",
    )

    preflight = _project(doc.get("preflight"), "preflight", PREFLIGHT_FIELDS)
    _require(
        preflight["frozen_contract_verified"] is True,
        "preflight.frozen_contract_verified is not true; the run did not confirm the frozen "
        "contract before calling the provider",
    )
    _require(
        preflight["cases_verified"] == EXPECTED_CASE_COUNT,
        f"preflight.cases_verified is {preflight['cases_verified']}, expected "
        f"{EXPECTED_CASE_COUNT}",
    )

    cost = _project(doc.get("cost"), "cost", COST_FIELDS)
    calls_by_stage = _project(cost["calls_by_stage"], "cost.calls_by_stage", STAGE_FIELDS)
    retries_by_stage = _project(cost["retries_by_stage"], "cost.retries_by_stage", STAGE_FIELDS)
    for name, block in (("calls_by_stage", calls_by_stage), ("retries_by_stage", retries_by_stage)):
        for stage, value in block.items():
            _require(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0,
                f"cost.{name}.{stage} must be a non-negative integer, got {value!r}",
            )

    summed_calls = sum(c["provider_calls"] for a in attempts for c in a["cases"])
    _require(
        summed_calls == cost["actual_calls"],
        f"per-case provider calls total {summed_calls} but cost.actual_calls is "
        f"{cost['actual_calls']}",
    )
    _require(
        sum(calls_by_stage.values()) == cost["actual_calls"],
        f"cost.calls_by_stage totals {sum(calls_by_stage.values())} but cost.actual_calls "
        f"is {cost['actual_calls']}",
    )
    for stage in STAGE_FIELDS:
        per_case = sum(c["retries_by_stage"][stage] for a in source_attempts for c in a["cases"])
        _require(
            per_case == retries_by_stage[stage],
            f"per-case retries for {stage} total {per_case} but cost.retries_by_stage["
            f"{stage!r}] is {retries_by_stage[stage]}",
        )

    distribution = _project(doc.get("distribution"), "distribution", DISTRIBUTION_FIELDS)
    ks = [a["k"] for a in attempts]
    _require(
        list(distribution["ordered_k"]) == ks,
        f"distribution.ordered_k {distribution['ordered_k']} disagrees with the attempt k "
        f"values {ks}",
    )
    _require(
        distribution["n"] == EXPECTED_CASE_COUNT,
        f"distribution.n is {distribution['n']}, expected {EXPECTED_CASE_COUNT}",
    )
    _require(
        list(distribution["ordered_rates"]) == [f"{k}/{EXPECTED_CASE_COUNT}" for k in ks],
        f"distribution.ordered_rates {distribution['ordered_rates']} disagrees with the "
        f"attempt k values {ks}",
    )
    _require(
        distribution["min_k"] == min(ks) and distribution["max_k"] == max(ks),
        f"distribution min/max ({distribution['min_k']}, {distribution['max_k']}) disagree "
        f"with ordered_k {ks}",
    )
    ordered = sorted(ks)
    # Odd counts have one median. For an even count the source convention is unknown, so
    # either middle value is accepted rather than guessed at.
    medians = {ordered[len(ordered) // 2]} if len(ordered) % 2 else {
        ordered[len(ordered) // 2 - 1], ordered[len(ordered) // 2]
    }
    _require(
        distribution["median_k"] in medians,
        f"distribution.median_k {distribution['median_k']} disagrees with ordered_k {ks}",
    )

    limits = doc.get("limits")
    _require(
        isinstance(limits, list) and all(isinstance(x, str) for x in limits),
        "limits is not a list of strings",
    )

    summary = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "run_type": RUN_TYPE,
        "derivation": {
            "generated_by": "harness.eval.redteam_public_summary",
            "source_schema": SOURCE_SCHEMA,
            "source_run_type": SOURCE_RUN_TYPE,
            "source_run_id": run_id,
            "source_commit_sha": run["git"]["commit_sha"],
            "source_worktree_dirty": run["git"]["dirty"],
            "source_artifact_digests": {
                SOURCE_JSON_FILENAME: f"sha256:{json_digest}",
                SOURCE_MD_FILENAME: f"sha256:{md_digest}",
            },
            "removed_fields": [
                "attempts[].cases[].triage_concerns (verbatim model output and the "
                "model-selected fields it names)",
                "attempts[].cases[].proposal_note",
                "attempts[].cases[].findings",
                "attempts[].cases[].system_findings (a count is published instead)",
                "attempts[].cases[].appropriate_action (the author label; it is published "
                "in the frozen corpus manifest, not here)",
                "run.python_executable",
            ],
            "note": (
                "Sanitized derivative of one immutable live-session artifact. No rerun, no "
                "provider call, no re-analysis, and no new metric. Decision counts and the "
                "repeat structure are tabulations of per-case fields the source already "
                "records; the Wilson intervals are republished exactly as the protocol "
                "recorded them."
            ),
        },
        "preflight": preflight,
        "provider": _project(doc.get("provider"), "provider", PROVIDER_FIELDS),
        "session": session,
        "cost": cost,
        "attempts": attempts,
        "repeat_structure": _repeat_structure(attempts),
        "distribution": distribution,
        "limits": list(limits),
    }

    _assert_no_forbidden_keys(summary)
    return summary


def render_json(summary: dict[str, Any]) -> str:
    """Serialize deterministically: sorted keys, fixed indent, trailing newline."""
    return json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------------------
# Publication — exclusive claim, never overwrite
# --------------------------------------------------------------------------------------
def _pre_claim_hook(final: Path) -> None:
    """No-op in production. Test seam only: monkeypatched to simulate a race in which a
    directory appears at ``final`` between build and claim."""
    return None


def read_source(session_dir: Path) -> tuple[bytes, bytes]:
    """Read both artifact files once each, returning their exact bytes."""
    json_path = session_dir / SOURCE_JSON_FILENAME
    md_path = session_dir / SOURCE_MD_FILENAME
    for path in (json_path, md_path):
        if not path.is_file():
            raise PublicSummaryIntegrityError(f"missing source artifact file: {path}")
    return json_path.read_bytes(), md_path.read_bytes()


def publish(session_dir: str | Path, out_dir: str | Path = DEFAULT_OUT_DIR) -> Path:
    """Build and publish the public summary for one pinned session.

    Guarantees:
    * On any integrity failure, nothing is created.
    * A pre-existing directory at the target path is never overwritten or reused
      (``PublicSummaryCollisionError``); the exclusive ``os.mkdir`` claim leaves it alone.
    * A post-claim write failure removes the claimed directory before re-raising.
    """
    session_dir = Path(session_dir)
    source_json, source_md = read_source(session_dir)
    summary = build_public_summary(source_json, source_md, expected_run_id=session_dir.name)
    text = render_json(summary)

    out_root = Path(out_dir)
    out_root_preexisted = out_root.exists()
    out_root.mkdir(parents=True, exist_ok=True)

    final = out_root / summary["derivation"]["source_run_id"]
    _pre_claim_hook(final)

    try:
        final.mkdir()  # exclusive claim: parents=False, exist_ok=False
    except FileExistsError as exc:
        raise PublicSummaryCollisionError(
            f"refusing to publish: {final} already exists. A published public summary is "
            "never overwritten or regenerated in place — it is pinned by digest and cited "
            "by URL. To rebuild for verification, publish to a scratch --out and compare "
            "digests."
        ) from exc

    try:
        (final / PUBLIC_JSON_FILENAME).write_text(text, encoding="utf-8", newline="\n")
    except Exception as original_exc:
        try:
            shutil.rmtree(final)
        except Exception as cleanup_exc:
            raise PublicSummaryCleanupError(
                f"publishing {final} failed and automatic cleanup of that directory also "
                f"failed. {final} is incomplete and is NOT a published summary — it must "
                "never be read or cited, and requires manual investigation and removal "
                "before this run_id is retried. Original publication failure: "
                f"{original_exc!r}. Cleanup failure: {cleanup_exc!r}.",
                original_error=original_exc,
                cleanup_error=cleanup_exc,
            ) from cleanup_exc
        if not out_root_preexisted:
            # Courtesy only: the claimed directory is gone, so nothing incomplete remains.
            # rmdir alone, never recursive, and a failure here is benign (most commonly
            # ENOTEMPTY from a concurrent sibling), so the original error propagates.
            try:
                out_root.rmdir()
            except OSError:
                pass
        raise
    return final


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Publish the sanitized public summary of one pinned P4(c) live session. "
            "Reads an immutable artifact, performs no provider call, and introduces no "
            "new metric."
        )
    )
    parser.add_argument("session_dir", help="directory holding the raw session artifact")
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT_DIR,
        help="tracked evidence root; a leaf named after the source run_id is appended",
    )
    args = parser.parse_args(argv)

    final = publish(args.session_dir, out_dir=args.out)
    published = final / PUBLIC_JSON_FILENAME
    data = published.read_bytes()
    print(f"published {published}")
    print(f"  bytes  {len(data)}")
    print(f"  sha256 {sha256_of_bytes(data)}")


if __name__ == "__main__":
    main()

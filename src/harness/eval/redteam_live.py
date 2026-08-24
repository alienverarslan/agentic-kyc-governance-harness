"""P4(c) — the key-gated LIVE triage measurement over the frozen red-team corpus.

This is the first and only P4 surface that produces an empirical number. P4(b) republishes
a by-construction ``30/30`` admission; this module asks the one question that admission
cannot answer: under the pinned configuration, does the LIVE AI-triage layer raise a domain
concern on cases the deterministic layer is blind to by construction?

What is measured, precisely
----------------------------
The TRIAGE LAYER, not "the AI". The synthesis proposal cannot change an outcome — the
guardrail decides — so the only semantic signal here is a finding with
``origin="ai_triage"``. The primary outcome is the CLEAN-RUN LIVE-TRIAGE INTERVENTION RATE:
``k/n`` over a fully clean attempt, where ``k`` counts cases whose final decision is not
``approve`` because of at least one domain triage finding. It is never called a
false-approval rate, a detection rate, or an accuracy. The labels it is read against remain
human-reviewed threat-model judgments, not externally established ground truth.

Whole-attempt validity, never a variable denominator
------------------------------------------------------
A terminal SYSTEM/T0 failure in ANY case invalidates the entire attempt. Contaminated cases
are never excluded to compute a rate over a clean subset: that would make denominator
membership depend on operational behavior and invite selective-exclusion bias. An invalid
attempt carries no rate, no interval, and no numerator/denominator at all — those fields do
not exist in its shape. A SYSTEM-origin non-approval is never counted as an intervention and
never as evidence against a false approval: both LLM nodes emit a T0 that escalates, so a
provider outage would otherwise manufacture a flawless-looking result out of pure failure.

Terminal session states
------------------------
``completed`` — three valid attempts; the only state carrying a distribution.
``operational_failure_exhausted`` — replacements or the attempt cap ran out.
``coverage_violation`` — a case produced a finding whose origin is neither ``ai_triage`` nor
``system``, at ANY severity. That disproves the frozen out-of-coverage premise for the
executed configuration; it is terminal, session-wide, and takes precedence over any
co-occurring T0. It is a result to investigate — the remedy is a versioned re-triage, never
an edit to a fixture, label, hash, or the frozen manifest.
``live_call_budget_exhausted`` — the session cost ceiling stopped the work. An operational
limitation, never evidence about triage.
``input_integrity_failure`` — a frozen case could not be loaded or executed AFTER preflight
had already validated the whole corpus. Distinct from ``coverage_violation``: the session
could not reliably execute its frozen input, rather than having observed evidence that
disproves the coverage boundary.

Cost control
-------------
Enforcement is RESERVATION, not detection: a case is never started unless the full
worst-case cost of a case (two call sites × three attempts = six outbound requests) still
fits under the session ceiling. The ceiling therefore cannot be exceeded, and no
budget exception exists — one would be caught by the graph's ``except Exception`` in the LLM
nodes and misreported as operational contamination.

Live calls are operator-initiated ONLY. No test in this project performs a provider call.
Without ``--confirm-live`` the CLI performs a free dry run: key check, frozen-contract
preflight, the full banner, zero provider calls, and no output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Type

from harness.agent import checks, coverage, triage
from harness.agent.runner import run_agent
from harness.data import redteam_corpus
from harness.eval import provenance
from harness.eval.redteam_record import validate_frozen_contract
from harness.eval.stats import RateStat
from harness.llm.anthropic_client import DEFAULT_MODEL, AnthropicClient
from harness.llm.errors import LLMError
from harness.rules.store import load_promoted_rules

SESSION_SCHEMA = "redteam_live_session"
SESSION_SCHEMA_VERSION = "1.0.0"
RUN_TYPE = "redteam_live_triage"

DEFAULT_OUT_DIR = "artifacts/p4c_live"
JSON_FILENAME = "redteam_live_session.json"
MD_FILENAME = "redteam_live_session.md"

# Session policy. Fixed: the session stops at three valid attempts regardless of how
# interesting the observed variability looks.
TARGET_VALID_ATTEMPTS = 3
MAX_REPLACEMENTS = 2
MAX_ATTEMPTS = 5

# Cost policy. CALL_SITES_PER_CASE is a property of the graph, which is strictly linear:
# `synthesize` is reached for every case, including one whose `ai_triage` call failed.
CALL_SITES_PER_CASE = 2
WRAPPER_MAX_ATTEMPTS_PER_CALL_SITE = 3
MAX_CALLS_PER_CASE = CALL_SITES_PER_CASE * WRAPPER_MAX_ATTEMPTS_PER_CALL_SITE  # 6
SESSION_CALL_CEILING = 600

# Provider pin. Recorded in provenance; ANTHROPIC_MODEL is deliberately NOT consulted, so a
# stray environment variable cannot silently change what was measured.
PINNED_MODEL = DEFAULT_MODEL
PINNED_TEMPERATURE = 0.0
PINNED_MAX_TOKENS = 1024
PROVIDER_ATTEMPT_TIMEOUT_S = 60.0
SDK_MAX_RETRIES = 0

# Retry policy. The 60s timeout bounds ONE SDK request, never a logical graph call: a call
# site may span three requests plus two bounded backoffs.
WRAPPER_BACKOFF_SCHEDULE_S = (1.0, 4.0)
WRAPPER_JITTER_CAP_S = 1.0
LOGICAL_CALL_UPPER_BOUND_S = (
    WRAPPER_MAX_ATTEMPTS_PER_CALL_SITE * PROVIDER_ATTEMPT_TIMEOUT_S
    + sum(WRAPPER_BACKOFF_SCHEDULE_S)
    + len(WRAPPER_BACKOFF_SCHEDULE_S) * WRAPPER_JITTER_CAP_S
)  # 187.0

RETRYABLE_KINDS = ("timeout", "provider_error")

STAGE_TRIAGE = "ai_triage"
STAGE_SYNTHESIZE = "synthesize"
STAGES = (STAGE_TRIAGE, STAGE_SYNTHESIZE)

PERMITTED_ORIGINS = ("ai_triage", "system")

UNTRUSTED_PROVENANCE = "model_generated_untrusted"
UNTRUSTED_NOTE = (
    "Verbatim model output. Not an established fact, not a verified finding, and not a "
    "project claim about this dossier."
)

TERMINAL_COMPLETED = "completed"
TERMINAL_OPERATIONAL_EXHAUSTED = "operational_failure_exhausted"
TERMINAL_COVERAGE_VIOLATION = "coverage_violation"
TERMINAL_BUDGET_EXHAUSTED = "live_call_budget_exhausted"
TERMINAL_INPUT_INTEGRITY = "input_integrity_failure"

BUDGET_NOTE = (
    "Budget exhaustion is an operational limitation of this session. It is never evidence "
    "of triage intervention, and never evidence of its absence."
)

_LIMITS = [
    "This session measures the live AI-triage layer only. The synthesis proposal cannot "
    "change an outcome; the deterministic guardrail decides.",
    "Rates here are not false-approval rates, not detection rates, and not accuracy. They "
    "are intervention counts over a fixed, author-constructed synthetic challenge set.",
    "Labels are human-reviewed threat-model judgments, not externally established "
    "real-world ground truth.",
    "Not comparable to the separate in-coverage holdout corpus surface, and not comparable "
    "to the by-construction admission republished by the separate offline record surface.",
    "Each attempt is reported on its own. Attempts are never pooled: the same cases repeat, "
    "so outcomes are correlated by case difficulty, prompt, and model state, and pooling "
    "would treat correlated observations as independent.",
    "The provider path pins temperature but exposes no seed. Repeated attempts estimate "
    "provider-side run-to-run variability, not seeded replication.",
    "A result of zero interventions is a valid measured outcome, not an implementation "
    "failure, and never a reason to adjust prompts, rules, labels, or fixtures.",
]


# --------------------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------------------
class RedTeamLiveError(Exception):
    """Base for every P4(c) session error."""


class LivePreflightError(RedTeamLiveError):
    """A precondition failed before any provider call and before any directory was
    created: the API key is absent, or the frozen contract no longer validates."""


class StageAttributionError(RedTeamLiveError):
    """A structured call arrived carrying a system prompt that is neither the pinned triage
    prompt nor the pinned synthesis prompt. Raised BEFORE any outbound request: a call that
    cannot be attributed to a stage must never be issued, and must never be silently
    labelled as one stage or the other."""


class SessionCollisionError(RedTeamLiveError):
    """The generated session directory already exists. A published session is never
    overwritten or reused; the pre-existing directory is left byte-for-byte intact."""


class SessionPublicationCleanupError(RedTeamLiveError):
    """A post-claim failure occurred AND cleanup of the claimed directory also failed. The
    incomplete directory is left on disk and requires manual investigation."""

    def __init__(
        self, message: str, *, original_error: BaseException, cleanup_error: BaseException
    ) -> None:
        super().__init__(message)
        self.original_error = original_error
        self.cleanup_error = cleanup_error


# --------------------------------------------------------------------------------------
# Cost accounting
# --------------------------------------------------------------------------------------
@dataclass
class CallCounter:
    """Session-scoped outbound-request accounting. ``total`` counts REQUESTS actually
    issued, never an estimate."""

    ceiling: int = SESSION_CALL_CEILING
    total: int = 0
    by_stage: dict[str, int] = field(default_factory=lambda: {s: 0 for s in STAGES})
    retries_by_stage: dict[str, int] = field(default_factory=lambda: {s: 0 for s in STAGES})

    def remaining(self) -> int:
        return self.ceiling - self.total

    def can_reserve(self, calls: int) -> bool:
        """The enforcement primitive: a case is only started when its full worst case
        still fits, so the ceiling can never be exceeded rather than merely detected."""
        return self.remaining() >= calls

    def record_call(self, stage: str) -> None:
        self.total += 1
        self.by_stage[stage] = self.by_stage.get(stage, 0) + 1

    def record_retry(self, stage: str) -> None:
        self.retries_by_stage[stage] = self.retries_by_stage.get(stage, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "actual_calls": self.total,
            "calls_by_stage": dict(self.by_stage),
            "retries_by_stage": dict(self.retries_by_stage),
        }


# --------------------------------------------------------------------------------------
# Provider wrapper — the ONLY retry layer
# --------------------------------------------------------------------------------------
class RetryingLLMClient:
    """Bounded retry around a single structured provider call.

    It retries THAT ONE CALL — never a node, never a case, never an attempt. Intermediate
    failures are swallowed here and never reach the graph, so they cannot become T0
    findings and cannot be seen by classification; only the final terminal exception
    propagates, where the node converts it into exactly one T0.

    Stage attribution is local and deterministic: the stage is derived inside this method
    from the system prompt by exact equality against the pinned prompts. The orchestrator
    cannot supply it, because ``run_agent`` drives the graph internally and there is no safe
    point outside it at which to switch context between the two call sites.
    """

    def __init__(
        self,
        inner: Any,
        counter: CallCounter,
        *,
        sleep=time.sleep,
        jitter=None,
    ) -> None:
        self._inner = inner
        self._counter = counter
        self._sleep = sleep
        self._jitter = jitter if jitter is not None else (lambda: random.uniform(0.0, WRAPPER_JITTER_CAP_S))

    @staticmethod
    def stage_for_system_prompt(system: str) -> str:
        if system == triage.TRIAGE_SYSTEM:
            return STAGE_TRIAGE
        if system == checks.SYNTHESIS_SYSTEM:
            return STAGE_SYNTHESIZE
        raise StageAttributionError(
            "structured call carries a system prompt matching neither the pinned triage "
            "prompt nor the pinned synthesis prompt; refusing to issue a provider request "
            "that cannot be attributed to a stage"
        )

    def complete_structured(self, system: str, user: str, schema: Type[Any]) -> Any:
        stage = self.stage_for_system_prompt(system)  # raises before any outbound request
        last_index = WRAPPER_MAX_ATTEMPTS_PER_CALL_SITE - 1
        for attempt in range(WRAPPER_MAX_ATTEMPTS_PER_CALL_SITE):
            self._counter.record_call(stage)
            try:
                return self._inner.complete_structured(system, user, schema)
            except LLMError as exc:
                if exc.kind not in RETRYABLE_KINDS or attempt == last_index:
                    raise
                self._counter.record_retry(stage)
                delay = WRAPPER_BACKOFF_SCHEDULE_S[attempt] + max(0.0, self._jitter())
                self._sleep(delay)
        raise AssertionError("unreachable: the retry loop always returns or raises")


# --------------------------------------------------------------------------------------
# Case-level classification
# --------------------------------------------------------------------------------------
CLASSIFICATION_INTERVENTION = "intervention"
CLASSIFICATION_NON_INTERVENTION = "non_intervention"
CLASSIFICATION_CONTAMINATED = "contaminated"
CLASSIFICATION_COVERAGE_VIOLATION = "coverage_violation"
CLASSIFICATION_NOT_ATTEMPTED = "not_attempted"


def _finding_row(finding: Any) -> dict[str, Any]:
    """One finding, serialized for audit. ``detail`` is included for every origin EXCEPT
    ``ai_triage``: that text is model-generated, and it is carried only inside
    ``triage_concerns``, where its untrusted-output marker cannot be separated from it."""
    row: dict[str, Any] = {
        "origin": finding.origin,
        "code": finding.code,
        "severity": finding.severity,
        "check_name": finding.check_name,
    }
    error_kind = getattr(finding, "error_kind", None)
    if error_kind:
        row["error_kind"] = error_kind
    if finding.origin != "ai_triage":
        row["detail"] = finding.detail
    return row


def _triage_concern_row(finding: Any) -> dict[str, Any]:
    return {
        "code": finding.code,
        "severity": finding.severity,
        "detail": finding.detail,
        "fields_involved": list(finding.fields_involved),
        "content_provenance": UNTRUSTED_PROVENANCE,
        "content_note": UNTRUSTED_NOTE,
    }


def classify_case(findings: list[Any]) -> str:
    """Strict precedence. A foreign-origin finding wins even when a T0 is also present:
    it disproves the frozen out-of-coverage premise for the executed configuration, which
    is a stronger and different fact than a provider problem."""
    if any(f.origin not in PERMITTED_ORIGINS for f in findings):
        return CLASSIFICATION_COVERAGE_VIOLATION
    if any(f.origin == "system" for f in findings):
        return CLASSIFICATION_CONTAMINATED
    if any(f.origin == "ai_triage" for f in findings):
        return CLASSIFICATION_INTERVENTION
    return CLASSIFICATION_NON_INTERVENTION


def coverage_violation_reason(findings: list[Any]) -> str:
    foreign = [f for f in findings if f.origin not in PERMITTED_ORIGINS]
    origins = {f.origin for f in foreign}
    checks_hit = {f.check_name for f in foreign}
    if "check_learned_rules" in checks_hit:
        return "learned_rule_finding_present"
    if origins == {"deterministic"}:
        return "deterministic_finding_present"
    return "unclassified_origin"


def _case_row(
    case_id: str,
    appropriate_action: str | None,
    result: Any,
    classification: str,
    calls_before: dict[str, Any],
    calls_after: dict[str, Any],
) -> dict[str, Any]:
    findings = list(result.findings)
    triage_findings = [f for f in findings if f.origin == "ai_triage"]
    system_findings = [f for f in findings if f.origin == "system"]
    return {
        "case_id": case_id,
        "appropriate_action": appropriate_action,
        "final_decision": result.decision,
        "classification": classification,
        "findings": [_finding_row(f) for f in findings],
        "triage_concerns": [_triage_concern_row(f) for f in triage_findings],
        "system_findings": [
            {
                "stage": f.check_name,
                "error_kind": getattr(f, "error_kind", None),
                "code": f.code,
            }
            for f in system_findings
        ],
        "provider_calls": calls_after["actual_calls"] - calls_before["actual_calls"],
        "retries_by_stage": {
            stage: calls_after["retries_by_stage"].get(stage, 0)
            - calls_before["retries_by_stage"].get(stage, 0)
            for stage in STAGES
        },
        "guardrail_overrode_proposal": bool(result.guardrail.overridden),
        "proposal_decision": getattr(result.proposal, "proposed_decision", None),
        "proposal_note": (
            "Recorded for audit only. The guardrail determines the final decision; the "
            "proposal never does."
        ),
    }


# --------------------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------------------
def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Preflight:
    manifest: dict[str, Any]
    promoted_rules: list[Any]
    case_ids: list[str]
    corpus_hash: str
    promoted_rules_hash: str
    prompt_hashes: dict[str, str]


def preflight() -> Preflight:
    """Every precondition, before any provider call and before any directory exists.

    Nothing is created here on success or failure. Configuration drift raises, exactly as
    the offline record surface does — the frozen admission describes the pinned
    configuration only, and a live measurement read against a different one would be a
    measurement of something else."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise LivePreflightError(
            "ANTHROPIC_API_KEY is not set. P4(c) is a live measurement and has no offline "
            "mode; nothing was created and no provider call was made."
        )
    try:
        manifest = validate_frozen_contract()
    except Exception as exc:  # noqa: BLE001 - re-raised as a preflight failure, chained
        raise LivePreflightError(
            f"the frozen red-team contract no longer validates: {exc}. No live session can "
            "be run against a configuration the frozen admission does not describe. The "
            "remedy is a separately designed versioned re-triage, never a re-freeze and "
            "never an edit to a fixture, label, canonical hash, or the frozen manifest."
        ) from exc

    promoted = load_promoted_rules()
    return Preflight(
        manifest=manifest,
        promoted_rules=promoted,
        case_ids=redteam_corpus.list_redteam_ids(),
        corpus_hash=manifest["corpus_hash"],
        promoted_rules_hash=manifest["pinned_config"]["promoted_rules_hash"],
        prompt_hashes={
            "triage_prompt_sha256": _sha256_text(triage.TRIAGE_SYSTEM),
            "synthesis_prompt_sha256": _sha256_text(checks.SYNTHESIS_SYSTEM),
            "coverage_catalog_prompt_sha256": _sha256_text(coverage.catalog_summary(promoted)),
        },
    )


def build_banner(pre: Preflight, out_dir: str | Path, *, confirm_live: bool) -> str:
    """Everything an operator must see BEFORE the first paid request."""
    n = len(pre.case_ids)
    lines = [
        "P4(c) live red-team triage measurement",
        "",
        f"  model                      {PINNED_MODEL}",
        f"  temperature                {PINNED_TEMPERATURE}",
        f"  max_tokens                 {PINNED_MAX_TOKENS}",
        f"  provider_attempt_timeout_s {PROVIDER_ATTEMPT_TIMEOUT_S}  (ONE SDK request)",
        f"  sdk_max_retries            {SDK_MAX_RETRIES}",
        f"  wrapper backoff            {list(WRAPPER_BACKOFF_SCHEDULE_S)} s "
        f"+ jitter cap {WRAPPER_JITTER_CAP_S} s",
        f"  logical call upper bound   {LOGICAL_CALL_UPPER_BOUND_S} s (includes jitter cap)",
        "",
        f"  policy                     {TARGET_VALID_ATTEMPTS} valid attempts, "
        f"{MAX_REPLACEMENTS} replacements, {MAX_ATTEMPTS} attempts max",
        f"  cases per attempt          {n}",
        f"  max outbound calls / clean attempt  {n * MAX_CALLS_PER_CASE}",
        f"  session outbound-call ceiling       {SESSION_CALL_CEILING}",
        "",
        f"  corpus_hash                {pre.corpus_hash}",
        f"  promoted_rules_hash        {pre.promoted_rules_hash}",
        f"  triage_prompt_sha256       {pre.prompt_hashes['triage_prompt_sha256']}",
        f"  synthesis_prompt_sha256    {pre.prompt_hashes['synthesis_prompt_sha256']}",
        f"  coverage_catalog_prompt_sha256  "
        f"{pre.prompt_hashes['coverage_catalog_prompt_sha256']}",
        "",
        f"  output root                {out_dir}",
        "  Artifacts are written to a LOCAL, gitignored directory and are never committed.",
    ]
    if not confirm_live:
        lines += [
            "",
            "Dry run complete; pass --confirm-live to make provider calls and publish a "
            "local session artifact.",
        ]
    return "\n".join(lines)


def _build_provider_client() -> Any:
    """The single provider-construction point, isolated so the pinned transport settings
    are explicit and provenance-recorded. Tests replace this function; no test ever reaches
    a real provider."""
    return AnthropicClient(
        model=PINNED_MODEL,
        temperature=PINNED_TEMPERATURE,
        max_tokens=PINNED_MAX_TOKENS,
        timeout=PROVIDER_ATTEMPT_TIMEOUT_S,
        max_retries=SDK_MAX_RETRIES,
    )


# --------------------------------------------------------------------------------------
# Attempt execution
# --------------------------------------------------------------------------------------
def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _integrity_failure(
    index: int,
    started: str,
    cases: list[dict[str, Any]],
    case_id: str,
    exc: BaseException,
    not_attempted: list[str],
) -> dict[str, Any]:
    """The typed ``input_integrity_failure`` attempt. Carries no rate, interval, numerator,
    or denominator — those fields do not exist in this shape."""
    return {
        "index": index,
        "type": "input_integrity_failure",
        "started_utc": started,
        "ended_utc": _utc_now_str(),
        "cases": cases,
        "failed_case_id": case_id,
        "exception_class": type(exc).__name__,
        "not_attempted_case_ids": list(not_attempted),
        "integrity_note": (
            "A frozen case could not be loaded or executed after preflight had already "
            "validated the complete corpus. This is an input-integrity failure, not "
            "operational contamination and not a coverage violation: a provider failure "
            "surfaces as a graph-returned SYSTEM finding, never as an escaping exception."
        ),
    }


def _run_one_attempt(
    index: int, pre: Preflight, client: Any, counter: CallCounter
) -> dict[str, Any]:
    """Execute one attempt over every case in fixed id order.

    Returns a typed attempt dict. Rate fields exist ONLY on a valid attempt — they are
    absent, not null, everywhere else.
    """
    started = _utc_now_str()
    cases: list[dict[str, Any]] = []
    attempted: list[str] = []

    for position, case_id in enumerate(pre.case_ids):
        if not counter.can_reserve(MAX_CALLS_PER_CASE):
            return {
                "index": index,
                "type": "operationally_invalid",
                "cause": "budget_exhausted",
                "started_utc": started,
                "ended_utc": _utc_now_str(),
                "cases": cases,
                "contaminated_case_ids": [],
                "failing_stages": [],
                "error_kinds": [],
                "not_attempted_case_ids": list(pre.case_ids[position:]),
                "budget_note": BUDGET_NOTE,
            }

        # Loading and executing are both covered: the terminal state is "could not be
        # loaded OR executed". A graph-returned T0 is ordinary contamination and is
        # classified below; only an exception that ESCAPES run_agent lands here.
        try:
            case = redteam_corpus.load_redteam_case(case_id)
        except Exception as exc:  # noqa: BLE001 - classified, never silently absorbed
            # The failing case was never started, so it is itself unattempted.
            return _integrity_failure(
                index, started, cases, case_id, exc, pre.case_ids[position:]
            )

        before = counter.snapshot()
        try:
            result = run_agent(
                case.dossier, client, case_ref=case_id, promoted_rules=pre.promoted_rules
            )
        except Exception as exc:  # noqa: BLE001 - classified, never silently absorbed
            # The failing case WAS started, so the unattempted list begins after it.
            return _integrity_failure(
                index, started, cases, case_id, exc, pre.case_ids[position + 1 :]
            )
        after = counter.snapshot()
        attempted.append(case_id)

        findings = list(result.findings)
        classification = classify_case(findings)
        row = _case_row(
            case_id, case.label.appropriate_action, result, classification, before, after
        )
        cases.append(row)

        if classification == CLASSIFICATION_COVERAGE_VIOLATION:
            return {
                "index": index,
                "type": "coverage_violation",
                "started_utc": started,
                "ended_utc": _utc_now_str(),
                "cases": cases,
                "not_attempted_case_ids": list(pre.case_ids[position + 1 :]),
                "evidence": {
                    "case_id": case_id,
                    "final_decision": result.decision,
                    "findings": [_finding_row(f) for f in findings],
                    "corpus_hash": pre.corpus_hash,
                    "promoted_rules_hash": pre.promoted_rules_hash,
                    "reason_code": coverage_violation_reason(findings),
                    "meaning": (
                        "The frozen out-of-coverage admission does not describe the "
                        "executed configuration. This is a result to investigate, never a "
                        "live-triage outcome, and never resolved by editing a fixture, "
                        "label, canonical hash, or the frozen manifest."
                    ),
                },
            }

        if classification == CLASSIFICATION_CONTAMINATED:
            system_findings = [f for f in findings if f.origin == "system"]
            return {
                "index": index,
                "type": "operationally_invalid",
                "cause": "system_contamination",
                "started_utc": started,
                "ended_utc": _utc_now_str(),
                "cases": cases,
                "contaminated_case_ids": [case_id],
                "failing_stages": sorted({f.check_name for f in system_findings}),
                "error_kinds": sorted(
                    {getattr(f, "error_kind", None) or "unknown" for f in system_findings}
                ),
                "not_attempted_case_ids": list(pre.case_ids[position + 1 :]),
            }

    k = sum(1 for c in cases if c["classification"] == CLASSIFICATION_INTERVENTION)
    rate = RateStat(k=k, n=len(cases))
    return {
        "index": index,
        "type": "valid",
        "started_utc": started,
        "ended_utc": _utc_now_str(),
        "cases": cases,
        **rate.to_dict(),
    }


# --------------------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------------------
def _generate_session_id() -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-p4c"


def _distribution(valid_attempts: list[dict[str, Any]]) -> dict[str, Any]:
    ks = [a["k"] for a in valid_attempts]
    n = valid_attempts[0]["n"]
    return {
        "ordered_rates": [f"{k}/{n}" for k in ks],
        "ordered_k": list(ks),
        "n": n,
        "min_k": min(ks),
        "max_k": max(ks),
        "median_k": statistics.median(ks),
        "pooling_note": (
            "Descriptive only, over attempt-level results. Numerators and denominators are "
            "never pooled and no interval is computed across attempts: the same cases "
            "repeat, so attempt outcomes are correlated."
        ),
    }


def run_session(pre: Preflight, counter: CallCounter, client: Any) -> dict[str, Any]:
    """Drive attempts until a terminal state is reached. Pure with respect to the
    filesystem: builds and returns the session record, writes nothing."""
    attempts: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    replacements_used = 0
    terminal = TERMINAL_OPERATIONAL_EXHAUSTED
    started = _utc_now_str()

    for index in range(1, MAX_ATTEMPTS + 1):
        attempt = _run_one_attempt(index, pre, client, counter)
        attempts.append(attempt)

        if attempt["type"] == "coverage_violation":
            terminal = TERMINAL_COVERAGE_VIOLATION
            break
        if attempt["type"] == "input_integrity_failure":
            terminal = TERMINAL_INPUT_INTEGRITY
            break
        if attempt["type"] == "valid":
            valid.append(attempt)
            if len(valid) == TARGET_VALID_ATTEMPTS:
                terminal = TERMINAL_COMPLETED
                break
            continue

        # operationally_invalid
        if attempt.get("cause") == "budget_exhausted":
            terminal = TERMINAL_BUDGET_EXHAUSTED
            break
        replacements_used += 1
        if replacements_used > MAX_REPLACEMENTS or index == MAX_ATTEMPTS:
            terminal = TERMINAL_OPERATIONAL_EXHAUSTED
            break

    session: dict[str, Any] = {
        "schema": SESSION_SCHEMA,
        "schema_version": SESSION_SCHEMA_VERSION,
        "run": provenance.build_run_provenance(
            run_type=RUN_TYPE, timestamp_utc=started, run_id=_generate_session_id()
        ),
        "session": {
            "started_utc": started,
            "ended_utc": _utc_now_str(),
            "terminal_state": terminal,
            "valid_attempts": len(valid),
            "invalid_attempts": sum(
                1 for a in attempts if a["type"] == "operationally_invalid"
            ),
            "replacements_used": replacements_used,
            "attempts_made": len(attempts),
            "target_valid_attempts": TARGET_VALID_ATTEMPTS,
            "max_replacements": MAX_REPLACEMENTS,
            "max_attempts": MAX_ATTEMPTS,
        },
        "preflight": {
            "frozen_contract_verified": True,
            "corpus_hash": pre.corpus_hash,
            "promoted_rules_hash": pre.promoted_rules_hash,
            "cases_verified": len(pre.case_ids),
        },
        "provider": {
            "model": PINNED_MODEL,
            "temperature": PINNED_TEMPERATURE,
            "max_tokens": PINNED_MAX_TOKENS,
            "provider_attempt_timeout_s": PROVIDER_ATTEMPT_TIMEOUT_S,
            "sdk_max_retries": SDK_MAX_RETRIES,
            "wrapper_max_attempts_per_call_site": WRAPPER_MAX_ATTEMPTS_PER_CALL_SITE,
            "wrapper_backoff_schedule_s": list(WRAPPER_BACKOFF_SCHEDULE_S),
            "wrapper_jitter_cap_s": WRAPPER_JITTER_CAP_S,
            "logical_call_upper_bound_s": LOGICAL_CALL_UPPER_BOUND_S,
            "timeout_scope_note": (
                "provider_attempt_timeout_s bounds ONE SDK request. A logical graph call "
                "may span three requests plus two bounded backoffs; "
                "logical_call_upper_bound_s includes the jitter cap."
            ),
            "seed_note": (
                "Temperature is pinned but the provider path exposes no seed. Repeated "
                "attempts estimate provider-side variability, not seeded replication."
            ),
            **pre.prompt_hashes,
        },
        "cost": {
            **counter.snapshot(),
            "max_calls_per_case": MAX_CALLS_PER_CASE,
            "max_calls_per_clean_attempt": len(pre.case_ids) * MAX_CALLS_PER_CASE,
            "session_call_ceiling": SESSION_CALL_CEILING,
            "ceiling_note": (
                "max_calls_per_clean_attempt is a theoretical per-attempt maximum. "
                "session_call_ceiling is a cost-stop policy, not a guarantee that the "
                "maximum number of fully retried attempts can complete."
            ),
        },
        "attempts": attempts,
        "limits": list(_LIMITS),
    }
    if terminal == TERMINAL_COMPLETED:
        session["distribution"] = _distribution(valid)
    return session


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------
def render_markdown(session: dict[str, Any]) -> str:
    s = session["session"]
    provider = session["provider"]
    cost = session["cost"]

    lines: list[str] = []
    lines.append("# P4(c) Live Red-Team Triage Session (key-gated, operator-initiated)")
    lines.append("")
    lines.append("## Limits")
    lines.append("")
    for item in session["limits"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Session")
    lines.append("")
    lines.append(f"- **terminal_state:** `{s['terminal_state']}`")
    lines.append(f"- **valid attempts:** {s['valid_attempts']} of {s['target_valid_attempts']}")
    lines.append(f"- **attempts made:** {s['attempts_made']} (max {s['max_attempts']})")
    lines.append(f"- **replacements used:** {s['replacements_used']} (max {s['max_replacements']})")
    lines.append("")
    if s["terminal_state"] != TERMINAL_COMPLETED:
        lines.append(
            "> This session published **no rate and no distribution**. Only a completed "
            "session carries a measured result."
        )
        lines.append("")

    lines.append("## Pinned configuration")
    lines.append("")
    for key in (
        "model",
        "temperature",
        "max_tokens",
        "provider_attempt_timeout_s",
        "sdk_max_retries",
        "wrapper_backoff_schedule_s",
        "wrapper_jitter_cap_s",
        "logical_call_upper_bound_s",
        "triage_prompt_sha256",
        "synthesis_prompt_sha256",
        "coverage_catalog_prompt_sha256",
    ):
        lines.append(f"- **{key}:** `{provider[key]}`")
    lines.append(f"- {provider['timeout_scope_note']}")
    lines.append(f"- {provider['seed_note']}")
    lines.append("")
    lines.append(f"- **corpus_hash:** `{session['preflight']['corpus_hash']}`")
    lines.append(f"- **promoted_rules_hash:** `{session['preflight']['promoted_rules_hash']}`")
    lines.append("")

    lines.append("## Cost")
    lines.append("")
    lines.append(f"- **actual outbound calls:** {cost['actual_calls']}")
    lines.append(f"- **by stage:** `{cost['calls_by_stage']}`")
    lines.append(f"- **retries by stage:** `{cost['retries_by_stage']}`")
    lines.append(
        f"- **max outbound calls per clean attempt:** {cost['max_calls_per_clean_attempt']}"
    )
    lines.append(f"- **session outbound-call ceiling:** {cost['session_call_ceiling']}")
    lines.append(f"- {cost['ceiling_note']}")
    lines.append("")

    for attempt in session["attempts"]:
        lines.append(f"## Attempt {attempt['index']} — {attempt['type']}")
        lines.append("")
        if attempt["type"] == "valid":
            ci = attempt["wilson_ci_95"]
            ci_text = f"[{ci[0]:.4f}, {ci[1]:.4f}]" if ci else "N/A"
            lines.append(f"Result: **{attempt['k']}/{attempt['n']}**, Wilson 95% {ci_text}")
            lines.append("")
        elif attempt["type"] == "operationally_invalid":
            lines.append(f"- **cause:** `{attempt['cause']}`")
            lines.append(f"- **contaminated cases:** `{attempt['contaminated_case_ids']}`")
            lines.append(f"- **failing stages:** `{attempt['failing_stages']}`")
            lines.append(f"- **error kinds:** `{attempt['error_kinds']}`")
            lines.append(f"- **not attempted:** {len(attempt['not_attempted_case_ids'])} cases")
            if attempt["cause"] == "budget_exhausted":
                lines.append(f"- {attempt['budget_note']}")
            lines.append("")
            lines.append("No rate is reported for an invalid attempt.")
            lines.append("")
        elif attempt["type"] == "coverage_violation":
            ev = attempt["evidence"]
            lines.append(f"- **case:** `{ev['case_id']}`")
            lines.append(f"- **final decision:** `{ev['final_decision']}`")
            lines.append(f"- **reason code:** `{ev['reason_code']}`")
            lines.append(f"- **findings:** `{ev['findings']}`")
            lines.append(f"- {ev['meaning']}")
            lines.append("")
        elif attempt["type"] == "input_integrity_failure":
            lines.append(f"- **case:** `{attempt['failed_case_id']}`")
            lines.append(f"- **exception class:** `{attempt['exception_class']}`")
            lines.append(f"- {attempt['integrity_note']}")
            lines.append("")

        concerns = [(c["case_id"], concern) for c in attempt["cases"] for concern in c["triage_concerns"]]
        if concerns:
            lines.append("Domain triage concerns raised in this attempt:")
            lines.append("")
            for case_id, concern in concerns:
                lines.append(f"- `{case_id}` ({concern['code']}/{concern['severity']}) — {concern['content_note']}")
                lines.append("")
                lines.append(f"  > {concern['detail']}")
                lines.append("")

    if "distribution" in session:
        dist = session["distribution"]
        lines.append("## Distribution across valid attempts")
        lines.append("")
        lines.append(f"- **ordered results:** `{dist['ordered_rates']}`")
        lines.append(f"- **min / median / max (k):** {dist['min_k']} / {dist['median_k']} / {dist['max_k']}")
        lines.append(f"- {dist['pooling_note']}")
        lines.append("")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------
# Publication
# --------------------------------------------------------------------------------------
def _is_valid_session_directory(path: Path) -> bool:
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


def _pre_claim_hook(final: Path) -> None:
    """No-op in production; test seam for the collision race."""
    return None


def publish_session(
    session: dict[str, Any], out_dir: str | Path = DEFAULT_OUT_DIR
) -> Path:
    """Write the session as one publication unit. Same lifecycle as the offline record
    surface: exclusive claim, never overwrite or reuse, Markdown last as the completion
    marker, best-effort non-recursive removal of a parent this invocation created."""
    md_text = render_markdown(session)
    json_text = json.dumps(session, indent=2, ensure_ascii=False) + "\n"

    out_root = Path(out_dir)
    out_root_preexisted = out_root.exists()
    out_root.mkdir(parents=True, exist_ok=True)

    final = out_root / session["run"]["run_id"]
    _pre_claim_hook(final)

    try:
        final.mkdir()
    except FileExistsError as exc:
        raise SessionCollisionError(
            f"refusing to publish: {final} already exists. A published session is never "
            "overwritten or reused."
        ) from exc

    try:
        (final / JSON_FILENAME).write_text(json_text, encoding="utf-8")
        (final / MD_FILENAME).write_text(md_text, encoding="utf-8")  # completion marker
    except Exception as original_exc:
        try:
            shutil.rmtree(final)
        except Exception as cleanup_exc:
            raise SessionPublicationCleanupError(
                f"publishing {final} failed and automatic cleanup of that directory also "
                f"failed. {final} is incomplete and is NOT a valid session record; it "
                "requires manual investigation before any retry. Original failure: "
                f"{original_exc!r}. Cleanup failure: {cleanup_exc!r}.",
                original_error=original_exc,
                cleanup_error=cleanup_exc,
            ) from cleanup_exc
        if not out_root_preexisted:
            try:
                out_root.rmdir()
            except OSError:
                pass
        raise

    return final


# --------------------------------------------------------------------------------------
# CLI — the SOLE live-call path
# --------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the P4(c) live red-team triage measurement. Without --confirm-live this "
            "performs a free dry run: preflight and banner only, zero provider calls, no "
            "output directory."
        )
    )
    parser.add_argument("--out", default=DEFAULT_OUT_DIR, help="parent output directory")
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help=(
            "make real provider calls and publish a local session artifact. Without this "
            "flag nothing is called and nothing is written."
        ),
    )
    args = parser.parse_args(argv)

    pre = preflight()
    print(build_banner(pre, args.out, confirm_live=args.confirm_live))
    if not args.confirm_live:
        return

    counter = CallCounter()
    client = RetryingLLMClient(_build_provider_client(), counter)
    session = run_session(pre, counter, client)
    final = publish_session(session, out_dir=args.out)
    print(f"\npublished {final}")
    print(f"  terminal_state: {session['session']['terminal_state']}")


if __name__ == "__main__":
    main()

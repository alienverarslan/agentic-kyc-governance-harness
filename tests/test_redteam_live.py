"""P4(c) — the live red-team triage session, tested entirely OFFLINE.

No test in this module performs a provider call, and none is key-gated: the sole live path
is the operator command ``harness-p4c-live --confirm-live``. Every provider interaction here
is a scripted fake, and one test asserts that the real provider-construction seam is never
reached on any offline path.

Two execution styles, deliberately:

* **Unit** — the retry wrapper, the classifier, the budget counter, and the renderer are
  exercised directly with fabricated findings and results. Fast, and precise about
  precedence rules that are hard to provoke through a whole graph.
* **Through the REAL graph** — the structural invariant "a domain triage concern always
  moves the outcome off approve" is proven by running ``run_agent`` on a real frozen
  red-team dossier with a scripted client, so the guardrail's actual severity policy is
  what is asserted, not a restatement of it.
"""

from __future__ import annotations

import json
import re
import sys

import pytest

from harness.contracts.findings import (
    AgentResult,
    Finding,
    GuardrailDecision,
    NovelConcern,
    SynthesisProposal,
    TriageResult,
)
from harness.data import redteam_corpus
from harness.eval import redteam_live
from harness.llm.errors import LLMError


# ========================================================================================
# Builders
# ========================================================================================
def _guardrail(decision):
    return GuardrailDecision(
        final_decision=decision,
        overridden=False,
        override_reason="",
        required_actions=[],
        finalization_mode="proposal_guarded",
    )


def _proposal(decision="approve"):
    return SynthesisProposal(
        status="available", proposed_decision=decision, reasoning="offline scripted"
    )


def _result(case_id, decision="approve", findings=()):
    return AgentResult(
        case_ref=case_id,
        decision=decision,
        guardrail=_guardrail(decision),
        proposal=_proposal(decision),
        findings=list(findings),
    )


def _triage_finding(code="X1", severity="explainable", detail="a model-authored concern"):
    return Finding(
        check_name="ai_triage",
        code=code,
        severity=severity,
        detail=detail,
        fields_involved=["registry.registered_address"],
        origin="ai_triage",
    )


def _system_finding(stage="ai_triage", error_kind="timeout"):
    return Finding(
        check_name=stage,
        code="T0",
        severity="unexplainable",
        detail="the model call timed out",
        fields_involved=[],
        origin="system",
        error_kind=error_kind,
    )


def _deterministic_finding(check_name="check_identity_consistency", severity="explainable"):
    return Finding(
        check_name=check_name,
        code="A1",
        severity=severity,
        detail="a deterministic finding that must not exist for an admitted P4 case",
        fields_involved=["legal_name"],
        origin="deterministic",
    )


def _preflight(case_ids=("rt_01", "rt_02")):
    return redteam_live.Preflight(
        manifest={},
        promoted_rules=[],
        case_ids=list(case_ids),
        corpus_hash="sha256:" + "a" * 64,
        promoted_rules_hash="sha256:" + "b" * 64,
        prompt_hashes={
            "triage_prompt_sha256": "sha256:" + "c" * 64,
            "synthesis_prompt_sha256": "sha256:" + "d" * 64,
            "coverage_catalog_prompt_sha256": "sha256:" + "e" * 64,
        },
    )


class _FakeCase:
    def __init__(self, case_id, action="request_more_info"):
        self.case_id = case_id
        self.dossier = {"dossier_id": case_id}
        self.label = type("L", (), {"appropriate_action": action})()


@pytest.fixture
def fake_cases(monkeypatch):
    monkeypatch.setattr(
        redteam_live.redteam_corpus, "load_redteam_case", lambda cid: _FakeCase(cid)
    )


def _script_agent(monkeypatch, outcomes):
    """Replace run_agent with a scripted per-case outcome map or callable."""
    calls: list[str] = []

    def _fake_run_agent(dossier, llm, *, case_ref=None, promoted_rules=None, store=None):
        calls.append(case_ref)
        value = outcomes(case_ref) if callable(outcomes) else outcomes[case_ref]
        return value

    monkeypatch.setattr(redteam_live, "run_agent", _fake_run_agent)
    return calls


def _rate_shaped_keys(obj, path=()):
    """Every key that looks like a measured rate, anywhere in a structure."""
    hits = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in {"k", "n", "point_estimate", "wilson_ci_95"} or key.endswith("_rate"):
                hits.append(path + (key,))
            hits.extend(_rate_shaped_keys(value, path + (key,)))
    elif isinstance(obj, list):
        for item in obj:
            hits.extend(_rate_shaped_keys(item, path))
    return hits


# ========================================================================================
# Group 1 — stage attribution and the retry wrapper
# ========================================================================================
class _ScriptedInner:
    """A provider stand-in. ``behaviors`` is a list consumed one entry per outbound call:
    an exception instance is raised, anything else is returned."""

    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.calls = []

    def complete_structured(self, system, user, schema):
        self.calls.append(system)
        behavior = self.behaviors.pop(0)
        if isinstance(behavior, BaseException):
            raise behavior
        return behavior


def _wrapper(behaviors, counter=None, sleeps=None):
    counter = counter or redteam_live.CallCounter()
    inner = _ScriptedInner(behaviors)
    recorded = sleeps if sleeps is not None else []
    client = redteam_live.RetryingLLMClient(
        inner, counter, sleep=recorded.append, jitter=lambda: redteam_live.WRAPPER_JITTER_CAP_S
    )
    return client, inner, counter, recorded


def test_each_pinned_prompt_maps_to_its_stage():
    from harness.agent import checks, triage

    stage_for = redteam_live.RetryingLLMClient.stage_for_system_prompt
    assert stage_for(triage.TRIAGE_SYSTEM) == redteam_live.STAGE_TRIAGE
    assert stage_for(checks.SYNTHESIS_SYSTEM) == redteam_live.STAGE_SYNTHESIZE


def test_unknown_prompt_raises_before_any_outbound_call():
    client, inner, counter, _ = _wrapper([TriageResult()])
    with pytest.raises(redteam_live.StageAttributionError):
        client.complete_structured("some other system prompt", "{}", TriageResult)
    assert inner.calls == []
    assert counter.total == 0


def test_retryable_failure_is_retried_and_leaves_no_visible_error():
    from harness.agent import triage

    client, inner, counter, sleeps = _wrapper(
        [LLMError("timeout"), TriageResult(novel_concerns=[])]
    )
    result = client.complete_structured(triage.TRIAGE_SYSTEM, "{}", TriageResult)
    assert isinstance(result, TriageResult)
    assert len(inner.calls) == 2
    assert counter.total == 2
    assert counter.retries_by_stage[redteam_live.STAGE_TRIAGE] == 1
    assert len(sleeps) == 1


def test_non_retryable_kind_raises_on_the_first_attempt():
    from harness.agent import triage

    client, inner, counter, sleeps = _wrapper([LLMError("schema_invalid")])
    with pytest.raises(LLMError):
        client.complete_structured(triage.TRIAGE_SYSTEM, "{}", TriageResult)
    assert len(inner.calls) == 1
    assert counter.total == 1
    assert counter.retries_by_stage[redteam_live.STAGE_TRIAGE] == 0
    assert sleeps == []


def test_only_the_terminal_exception_escapes_after_the_attempt_budget():
    from harness.agent import checks

    client, inner, counter, sleeps = _wrapper(
        [LLMError("timeout"), LLMError("provider_error"), LLMError("timeout")]
    )
    with pytest.raises(LLMError) as excinfo:
        client.complete_structured(checks.SYNTHESIS_SYSTEM, "{}", SynthesisProposal)
    assert excinfo.value.kind == "timeout"
    assert len(inner.calls) == redteam_live.WRAPPER_MAX_ATTEMPTS_PER_CALL_SITE == 3
    assert counter.by_stage[redteam_live.STAGE_SYNTHESIZE] == 3
    assert counter.retries_by_stage[redteam_live.STAGE_SYNTHESIZE] == 2
    assert len(sleeps) == 2


def test_backoff_is_bounded_by_the_schedule_plus_the_jitter_cap():
    from harness.agent import triage

    client, _inner, _counter, sleeps = _wrapper(
        [LLMError("timeout"), LLMError("timeout"), LLMError("timeout")]
    )
    with pytest.raises(LLMError):
        client.complete_structured(triage.TRIAGE_SYSTEM, "{}", TriageResult)
    schedule = redteam_live.WRAPPER_BACKOFF_SCHEDULE_S
    cap = redteam_live.WRAPPER_JITTER_CAP_S
    assert len(sleeps) == len(schedule)
    for delay, base in zip(sleeps, schedule):
        assert base <= delay <= base + cap


def test_documented_logical_call_bound_includes_the_jitter_cap():
    expected = (
        redteam_live.WRAPPER_MAX_ATTEMPTS_PER_CALL_SITE
        * redteam_live.PROVIDER_ATTEMPT_TIMEOUT_S
        + sum(redteam_live.WRAPPER_BACKOFF_SCHEDULE_S)
        + len(redteam_live.WRAPPER_BACKOFF_SCHEDULE_S) * redteam_live.WRAPPER_JITTER_CAP_S
    )
    assert redteam_live.LOGICAL_CALL_UPPER_BOUND_S == expected == 187.0


# ========================================================================================
# Group 2 — classification precedence
# ========================================================================================
def test_foreign_origin_finding_wins_over_a_co_occurring_system_finding():
    findings = [_system_finding(), _deterministic_finding()]
    assert redteam_live.classify_case(findings) == redteam_live.CLASSIFICATION_COVERAGE_VIOLATION


def test_system_finding_alone_is_contamination():
    assert (
        redteam_live.classify_case([_system_finding()])
        == redteam_live.CLASSIFICATION_CONTAMINATED
    )


def test_triage_finding_alone_is_an_intervention():
    assert (
        redteam_live.classify_case([_triage_finding()])
        == redteam_live.CLASSIFICATION_INTERVENTION
    )


def test_no_findings_is_a_non_intervention():
    assert redteam_live.classify_case([]) == redteam_live.CLASSIFICATION_NON_INTERVENTION


def test_an_info_severity_foreign_finding_is_still_a_coverage_violation():
    """Severity-independent on purpose: the frozen admission asserts ZERO findings of any
    origin, so an info-severity deterministic finding disproves it even though it changes
    no decision."""
    info = Finding(
        check_name="check_completeness",
        code="E1",
        severity="info",
        detail="informational only",
        fields_involved=[],
        origin="deterministic",
    )
    assert redteam_live.classify_case([info]) == redteam_live.CLASSIFICATION_COVERAGE_VIOLATION


def test_coverage_violation_reason_codes():
    assert (
        redteam_live.coverage_violation_reason([_deterministic_finding()])
        == "deterministic_finding_present"
    )
    learned = _deterministic_finding(check_name="check_learned_rules")
    assert redteam_live.coverage_violation_reason([learned]) == "learned_rule_finding_present"


# ========================================================================================
# Group 3 — the structural invariant, proven through the REAL graph
# ========================================================================================
class _ConcernRaisingLLM:
    """Offline client: raises one triage concern, then proposes approve. Feeding this to
    the real graph proves the guardrail — not this test — forces a non-approve outcome."""

    def __init__(self, severity="explainable"):
        self.severity = severity

    def complete_structured(self, system, user, schema):
        if schema is TriageResult:
            return TriageResult(
                novel_concerns=[
                    NovelConcern(detail="planted concern", severity=self.severity)
                ]
            )
        return SynthesisProposal(
            status="available", proposed_decision="approve", reasoning="offline"
        )


@pytest.mark.parametrize(
    "severity,expected",
    [("explainable", "request_more_info"), ("unexplainable", "escalate")],
)
def test_a_domain_triage_concern_always_moves_the_outcome_off_approve(severity, expected):
    from harness.agent.runner import run_agent

    dossier = redteam_corpus.load_redteam_dossier("rt_01")
    result = run_agent(dossier, _ConcernRaisingLLM(severity), promoted_rules=[])
    assert result.decision == expected
    assert result.decision != "approve"
    assert redteam_live.classify_case(list(result.findings)) == (
        redteam_live.CLASSIFICATION_INTERVENTION
    )


# ========================================================================================
# Group 4 — attempt execution, budget reservation, and shapes
# ========================================================================================
def test_a_clean_attempt_is_valid_and_carries_the_rate(monkeypatch, fake_cases):
    pre = _preflight()
    _script_agent(
        monkeypatch,
        {
            "rt_01": _result("rt_01", "request_more_info", [_triage_finding()]),
            "rt_02": _result("rt_02", "approve"),
        },
    )
    attempt = redteam_live._run_one_attempt(1, pre, object(), redteam_live.CallCounter())
    assert attempt["type"] == "valid"
    assert attempt["k"] == 1
    assert attempt["n"] == 2
    assert attempt["wilson_ci_95"] is not None
    assert [c["classification"] for c in attempt["cases"]] == [
        redteam_live.CLASSIFICATION_INTERVENTION,
        redteam_live.CLASSIFICATION_NON_INTERVENTION,
    ]


def test_contamination_aborts_the_attempt_and_carries_no_rate(monkeypatch, fake_cases):
    pre = _preflight(("rt_01", "rt_02", "rt_03"))
    calls = _script_agent(
        monkeypatch,
        {
            "rt_01": _result("rt_01", "approve"),
            "rt_02": _result("rt_02", "escalate", [_system_finding("synthesize", "provider_error")]),
            "rt_03": _result("rt_03", "approve"),
        },
    )
    attempt = redteam_live._run_one_attempt(1, pre, object(), redteam_live.CallCounter())
    assert attempt["type"] == "operationally_invalid"
    assert attempt["cause"] == "system_contamination"
    assert attempt["contaminated_case_ids"] == ["rt_02"]
    assert attempt["failing_stages"] == ["synthesize"]
    assert attempt["error_kinds"] == ["provider_error"]
    assert attempt["not_attempted_case_ids"] == ["rt_03"]
    assert calls == ["rt_01", "rt_02"], "cases after contamination must not be executed"
    assert _rate_shaped_keys(attempt) == []


def test_coverage_violation_is_terminal_and_records_full_evidence(monkeypatch, fake_cases):
    pre = _preflight(("rt_01", "rt_02"))
    _script_agent(
        monkeypatch,
        {
            "rt_01": _result(
                "rt_01",
                "request_more_info",
                [_deterministic_finding(), _system_finding()],
            ),
            "rt_02": _result("rt_02", "approve"),
        },
    )
    attempt = redteam_live._run_one_attempt(1, pre, object(), redteam_live.CallCounter())
    assert attempt["type"] == "coverage_violation"
    evidence = attempt["evidence"]
    assert evidence["case_id"] == "rt_01"
    assert evidence["final_decision"] == "request_more_info"
    assert evidence["reason_code"] == "deterministic_finding_present"
    origins = {f["origin"] for f in evidence["findings"]}
    assert origins == {"deterministic", "system"}, "system evidence is retained too"
    assert evidence["corpus_hash"] == pre.corpus_hash
    assert _rate_shaped_keys(attempt) == []


def test_post_preflight_case_load_failure_is_an_input_integrity_failure(monkeypatch):
    """A case that cannot be LOADED was never started, so it is itself unattempted."""
    pre = _preflight(("rt_01", "rt_02"))

    def _boom(cid):
        if cid == "rt_02":
            raise OSError("frozen case vanished after preflight")
        return _FakeCase(cid)

    monkeypatch.setattr(redteam_live.redteam_corpus, "load_redteam_case", _boom)
    _script_agent(monkeypatch, lambda cid: _result(cid, "approve"))
    attempt = redteam_live._run_one_attempt(1, pre, object(), redteam_live.CallCounter())
    assert attempt["type"] == "input_integrity_failure"
    assert attempt["failed_case_id"] == "rt_02"
    assert attempt["exception_class"] == "OSError"
    assert attempt["not_attempted_case_ids"] == ["rt_02"]
    assert [c["case_id"] for c in attempt["cases"]] == ["rt_01"]
    assert _rate_shaped_keys(attempt) == []


def test_an_exception_escaping_run_agent_is_an_input_integrity_failure(monkeypatch, fake_cases):
    """The terminal state covers execution, not only loading. A provider failure never
    reaches here — the graph converts it into a SYSTEM finding, which is ordinary
    contamination — so an escaping exception means the frozen input could not be executed."""
    pre = _preflight(("rt_01", "rt_02"))

    def _explode(dossier, llm, *, case_ref=None, promoted_rules=None, store=None):
        raise RuntimeError("the graph blew up")

    monkeypatch.setattr(redteam_live, "run_agent", _explode)
    attempt = redteam_live._run_one_attempt(1, pre, object(), redteam_live.CallCounter())
    assert attempt["type"] == "input_integrity_failure"
    assert attempt["type"] != "operationally_invalid"
    assert attempt["failed_case_id"] == "rt_01"
    assert attempt["exception_class"] == "RuntimeError"
    assert attempt["cases"] == []
    assert attempt["not_attempted_case_ids"] == ["rt_02"]
    assert "loaded or executed" in attempt["integrity_note"]
    assert _rate_shaped_keys(attempt) == []


def test_an_execution_failure_preserves_completed_rows_and_remaining_ids(
    monkeypatch, fake_cases
):
    """A case that WAS started is not itself unattempted: the remaining list begins after
    it, and every case completed before the failure is retained."""
    pre = _preflight(("rt_01", "rt_02", "rt_03", "rt_04"))

    def _explode_on_second(dossier, llm, *, case_ref=None, promoted_rules=None, store=None):
        if case_ref == "rt_02":
            raise ValueError("execution failed mid-attempt")
        return _result(case_ref, "request_more_info", [_triage_finding()])

    monkeypatch.setattr(redteam_live, "run_agent", _explode_on_second)
    attempt = redteam_live._run_one_attempt(1, pre, object(), redteam_live.CallCounter())
    assert attempt["type"] == "input_integrity_failure"
    assert attempt["failed_case_id"] == "rt_02"
    assert attempt["exception_class"] == "ValueError"
    assert [c["case_id"] for c in attempt["cases"]] == ["rt_01"]
    assert attempt["cases"][0]["classification"] == redteam_live.CLASSIFICATION_INTERVENTION
    assert attempt["not_attempted_case_ids"] == ["rt_03", "rt_04"]
    assert _rate_shaped_keys(attempt) == []


def test_an_execution_failure_ends_the_session_without_a_result(monkeypatch, fake_cases):
    pre = _preflight(("rt_01",))
    monkeypatch.setattr(
        redteam_live,
        "run_agent",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    session = redteam_live.run_session(pre, redteam_live.CallCounter(), object())
    assert session["session"]["terminal_state"] == redteam_live.TERMINAL_INPUT_INTEGRITY
    assert "distribution" not in session
    assert len(session["attempts"]) == 1


def test_a_case_is_never_started_when_fewer_than_six_calls_remain(monkeypatch, fake_cases):
    pre = _preflight(("rt_01", "rt_02"))
    counter = redteam_live.CallCounter(ceiling=redteam_live.MAX_CALLS_PER_CASE + 1)
    calls = _script_agent(monkeypatch, lambda cid: _result(cid, "approve"))

    def _consume(dossier, llm, *, case_ref=None, promoted_rules=None, store=None):
        for _ in range(2):
            counter.record_call(redteam_live.STAGE_TRIAGE)
        calls.append(case_ref)
        return _result(case_ref, "approve")

    monkeypatch.setattr(redteam_live, "run_agent", _consume)
    attempt = redteam_live._run_one_attempt(1, pre, object(), counter)
    assert calls == ["rt_01"], "the second case must never start"
    assert attempt["type"] == "operationally_invalid"
    assert attempt["cause"] == "budget_exhausted"
    assert attempt["not_attempted_case_ids"] == ["rt_02"]
    assert redteam_live.BUDGET_NOTE in attempt["budget_note"]
    assert _rate_shaped_keys(attempt) == []


def test_the_counter_never_exceeds_the_ceiling(monkeypatch, fake_cases):
    pre = _preflight(tuple(f"rt_{i:02d}" for i in range(1, 21)))
    ceiling = 20
    counter = redteam_live.CallCounter(ceiling=ceiling)

    def _consume(dossier, llm, *, case_ref=None, promoted_rules=None, store=None):
        for _ in range(redteam_live.MAX_CALLS_PER_CASE):
            counter.record_call(redteam_live.STAGE_TRIAGE)
        return _result(case_ref, "approve")

    monkeypatch.setattr(redteam_live, "run_agent", _consume)
    redteam_live._run_one_attempt(1, pre, object(), counter)
    assert counter.total <= ceiling


def test_reservation_requires_the_full_worst_case_cost_of_a_case():
    counter = redteam_live.CallCounter(ceiling=10)
    counter.total = 5
    assert counter.can_reserve(redteam_live.MAX_CALLS_PER_CASE) is False
    counter.total = 4
    assert counter.can_reserve(redteam_live.MAX_CALLS_PER_CASE) is True


# ========================================================================================
# Group 5 — session state machine
# ========================================================================================
def _session(monkeypatch, attempt_types, case_ids=("rt_01",), counter=None):
    """Drive run_session with a scripted sequence of per-attempt behaviors."""
    pre = _preflight(case_ids)
    sequence = list(attempt_types)
    state = {"i": 0}

    def _outcome(case_ref):
        kind = sequence[state["i"]]
        if kind == "valid":
            return _result(case_ref, "request_more_info", [_triage_finding()])
        if kind == "clean":
            return _result(case_ref, "approve")
        if kind == "contaminated":
            return _result(case_ref, "escalate", [_system_finding()])
        if kind == "coverage":
            return _result(case_ref, "request_more_info", [_deterministic_finding()])
        raise AssertionError(kind)

    def _fake_run_agent(dossier, llm, *, case_ref=None, promoted_rules=None, store=None):
        return _outcome(case_ref)

    monkeypatch.setattr(redteam_live, "run_agent", _fake_run_agent)
    monkeypatch.setattr(
        redteam_live.redteam_corpus, "load_redteam_case", lambda cid: _FakeCase(cid)
    )

    real_attempt = redteam_live._run_one_attempt

    def _tracked(index, pre_, client, counter_):
        state["i"] = index - 1
        return real_attempt(index, pre_, client, counter_)

    monkeypatch.setattr(redteam_live, "_run_one_attempt", _tracked)
    return redteam_live.run_session(pre, counter or redteam_live.CallCounter(), object())


def test_three_valid_attempts_complete_the_session(monkeypatch):
    session = _session(monkeypatch, ["valid", "valid", "valid"])
    assert session["session"]["terminal_state"] == redteam_live.TERMINAL_COMPLETED
    assert session["session"]["valid_attempts"] == 3
    assert len(session["attempts"]) == 3
    assert "distribution" in session


def test_the_session_stops_at_three_valid_attempts(monkeypatch):
    session = _session(monkeypatch, ["valid"] * 5)
    assert len(session["attempts"]) == redteam_live.TARGET_VALID_ATTEMPTS == 3


def test_a_contaminated_attempt_is_replaced_and_counted(monkeypatch):
    session = _session(monkeypatch, ["contaminated", "valid", "valid", "valid"])
    assert session["session"]["terminal_state"] == redteam_live.TERMINAL_COMPLETED
    assert session["session"]["replacements_used"] == 1
    assert session["session"]["invalid_attempts"] == 1
    assert len(session["attempts"]) == 4


def test_exhausting_replacements_ends_the_session_without_a_result(monkeypatch):
    session = _session(monkeypatch, ["contaminated"] * 3)
    assert session["session"]["terminal_state"] == redteam_live.TERMINAL_OPERATIONAL_EXHAUSTED
    assert "distribution" not in session
    assert session["session"]["valid_attempts"] == 0


def test_the_attempt_cap_is_never_exceeded(monkeypatch):
    session = _session(monkeypatch, ["contaminated", "valid", "contaminated", "valid", "valid"])
    assert len(session["attempts"]) <= redteam_live.MAX_ATTEMPTS


def test_a_coverage_violation_ends_the_session_immediately(monkeypatch):
    session = _session(monkeypatch, ["coverage", "valid", "valid", "valid"])
    assert session["session"]["terminal_state"] == redteam_live.TERMINAL_COVERAGE_VIOLATION
    assert len(session["attempts"]) == 1
    assert "distribution" not in session


def test_budget_exhaustion_is_its_own_terminal_state(monkeypatch):
    counter = redteam_live.CallCounter(ceiling=0)
    session = _session(monkeypatch, ["valid"], counter=counter)
    assert session["session"]["terminal_state"] == redteam_live.TERMINAL_BUDGET_EXHAUSTED
    assert "distribution" not in session
    assert session["cost"]["actual_calls"] == 0


def test_input_integrity_failure_is_its_own_terminal_state(monkeypatch):
    pre = _preflight(("rt_01",))
    monkeypatch.setattr(
        redteam_live.redteam_corpus,
        "load_redteam_case",
        lambda cid: (_ for _ in ()).throw(ValueError("corrupt")),
    )
    session = redteam_live.run_session(pre, redteam_live.CallCounter(), object())
    assert session["session"]["terminal_state"] == redteam_live.TERMINAL_INPUT_INTEGRITY
    assert "distribution" not in session
    assert session["attempts"][0]["exception_class"] == "ValueError"


# ========================================================================================
# Group 6 — session schema, statistics, and prohibited shapes
# ========================================================================================
def test_only_valid_attempts_carry_rate_shaped_keys(monkeypatch):
    session = _session(monkeypatch, ["contaminated", "valid", "valid", "valid"])
    for attempt in session["attempts"]:
        hits = _rate_shaped_keys(attempt)
        if attempt["type"] == "valid":
            assert {h[-1] for h in hits} >= {"k", "n", "point_estimate", "wilson_ci_95"}
        else:
            assert hits == [], f"{attempt['type']} must carry no rate-shaped key: {hits}"


def test_the_distribution_is_descriptive_only_and_never_pooled(monkeypatch):
    session = _session(monkeypatch, ["valid", "valid", "valid"])
    dist = session["distribution"]
    assert set(dist) == {
        "ordered_rates",
        "ordered_k",
        "n",
        "min_k",
        "max_k",
        "median_k",
        "pooling_note",
    }
    assert len(dist["ordered_rates"]) == 3
    assert "point_estimate" not in dist and "wilson_ci_95" not in dist


def test_no_pooled_denominator_appears_anywhere_in_the_session(monkeypatch):
    session = _session(monkeypatch, ["valid", "valid", "valid"])
    dumped = json.dumps(session)
    n = session["attempts"][0]["n"]
    pooled = f"{n * 3}"
    assert f'"n": {pooled}' not in dumped


def test_cost_block_distinguishes_the_per_attempt_maximum_from_the_session_ceiling(monkeypatch):
    session = _session(monkeypatch, ["valid", "valid", "valid"])
    cost = session["cost"]
    assert cost["max_calls_per_clean_attempt"] == 1 * redteam_live.MAX_CALLS_PER_CASE
    assert cost["session_call_ceiling"] == redteam_live.SESSION_CALL_CEILING
    assert cost["max_calls_per_clean_attempt"] != cost["session_call_ceiling"]
    assert "cost-stop policy" in cost["ceiling_note"]


def test_the_full_corpus_per_attempt_maximum_is_the_documented_formula():
    assert redteam_corpus.EXPECTED_CASE_COUNT * redteam_live.MAX_CALLS_PER_CASE == 180
    assert redteam_live.SESSION_CALL_CEILING == 600


def test_provider_block_records_the_pinned_transport_and_prompt_identity(monkeypatch):
    session = _session(monkeypatch, ["valid", "valid", "valid"])
    provider = session["provider"]
    assert provider["temperature"] == 0.0
    assert provider["max_tokens"] == 1024
    assert provider["provider_attempt_timeout_s"] == 60.0
    assert provider["sdk_max_retries"] == 0
    assert provider["wrapper_backoff_schedule_s"] == [1.0, 4.0]
    assert provider["wrapper_jitter_cap_s"] == 1.0
    assert provider["logical_call_upper_bound_s"] == 187.0
    for key in (
        "triage_prompt_sha256",
        "synthesis_prompt_sha256",
        "coverage_catalog_prompt_sha256",
    ):
        assert provider[key].startswith("sha256:")
    assert "no seed" in provider["seed_note"]


def test_limits_state_the_bounded_claim(monkeypatch):
    session = _session(monkeypatch, ["valid", "valid", "valid"])
    text = " ".join(session["limits"]).lower()
    assert "not false-approval rates" in text
    assert "threat-model judgments" in text
    assert "never pooled" in text
    assert "zero interventions is a valid measured outcome" in text


# ========================================================================================
# Group 7 — untrusted model output
# ========================================================================================
def test_every_triage_concern_carries_the_untrusted_output_marker(monkeypatch):
    session = _session(monkeypatch, ["valid", "valid", "valid"])
    concerns = [c for a in session["attempts"] for case in a["cases"] for c in case["triage_concerns"]]
    assert concerns
    for concern in concerns:
        assert concern["content_provenance"] == "model_generated_untrusted"
        assert "not an established fact" in concern["content_note"].lower()


def test_finding_rows_never_carry_unmarked_model_text(monkeypatch):
    session = _session(monkeypatch, ["valid", "valid", "valid"])
    for attempt in session["attempts"]:
        for case in attempt["cases"]:
            for finding in case["findings"]:
                if finding["origin"] == "ai_triage":
                    assert "detail" not in finding


def test_markdown_never_quotes_model_text_without_its_note(monkeypatch):
    session = _session(monkeypatch, ["valid", "valid", "valid"])
    md = redteam_live.render_markdown(session)
    lines = md.splitlines()
    quotes = [i for i, line in enumerate(lines) if line.strip().startswith("> ")]
    assert quotes, "the fixture raises concerns, so quoted model text must be rendered"
    for i in quotes:
        previous = [line for line in lines[:i] if line.strip()][-1]
        assert "not an established fact" in previous.lower(), (
            f"quoted model text at line {i} is not immediately preceded by its "
            f"untrusted-output note: {previous!r}"
        )


# ========================================================================================
# Group 8 — preflight, dry run, and the CLI
# ========================================================================================
def test_preflight_without_a_key_raises_and_creates_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(redteam_live.LivePreflightError, match="ANTHROPIC_API_KEY"):
        redteam_live.main(["--out", str(tmp_path / "out"), "--confirm-live"])
    assert not (tmp_path / "out").exists()


def test_preflight_drift_raises_and_creates_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _drift():
        raise RuntimeError("promoted-rule hash drift")

    monkeypatch.setattr(redteam_live, "validate_frozen_contract", _drift)
    with pytest.raises(redteam_live.LivePreflightError, match="versioned re-triage"):
        redteam_live.main(["--out", str(tmp_path / "out"), "--confirm-live"])
    assert not (tmp_path / "out").exists()


def test_dry_run_prints_the_banner_makes_no_call_and_writes_nothing(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(redteam_live, "validate_frozen_contract", lambda: _fake_manifest())
    monkeypatch.setattr(
        redteam_live,
        "_build_provider_client",
        lambda: pytest.fail("a dry run must never construct a provider client"),
    )
    out = tmp_path / "out"
    redteam_live.main(["--out", str(out)])
    printed = capsys.readouterr().out
    assert "Dry run complete; pass --confirm-live" in printed
    assert "session outbound-call ceiling" in printed
    assert "never committed" in printed
    assert not out.exists()


def _fake_manifest():
    return {
        "corpus_hash": "sha256:" + "a" * 64,
        "pinned_config": {"promoted_rules_hash": "sha256:" + "b" * 64},
    }


def test_confirm_live_publishes_exactly_two_files(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(redteam_live, "validate_frozen_contract", lambda: _fake_manifest())
    monkeypatch.setattr(redteam_live, "_build_provider_client", lambda: object())
    monkeypatch.setattr(
        redteam_live.redteam_corpus, "list_redteam_ids", lambda: ["rt_01", "rt_02"]
    )
    monkeypatch.setattr(
        redteam_live.redteam_corpus, "load_redteam_case", lambda cid: _FakeCase(cid)
    )
    monkeypatch.setattr(
        redteam_live,
        "run_agent",
        lambda dossier, llm, **kw: _result(kw.get("case_ref"), "approve"),
    )
    out = tmp_path / "out"
    redteam_live.main(["--out", str(out), "--confirm-live"])
    sessions = list(out.iterdir())
    assert len(sessions) == 1
    assert redteam_live._is_valid_session_directory(sessions[0]) is True
    payload = json.loads(
        (sessions[0] / redteam_live.JSON_FILENAME).read_text(encoding="utf-8")
    )
    assert payload["schema"] == "redteam_live_session"
    assert payload["session"]["terminal_state"] == redteam_live.TERMINAL_COMPLETED
    assert payload["distribution"]["ordered_rates"] == ["0/2", "0/2", "0/2"]


def test_zero_interventions_completes_the_session_as_a_measured_result(monkeypatch):
    """Every case approving — the triage layer catching nothing — is a COMPLETED session
    carrying a real 0/n result with its interval, not an implementation failure and not a
    reason to re-run."""
    session = _session(monkeypatch, ["clean", "clean", "clean"])
    assert session["session"]["terminal_state"] == redteam_live.TERMINAL_COMPLETED
    assert [a["k"] for a in session["attempts"]] == [0, 0, 0]
    assert session["distribution"]["ordered_rates"] == ["0/1", "0/1", "0/1"]
    assert all(a["wilson_ci_95"] is not None for a in session["attempts"])
    md = redteam_live.render_markdown(session)
    assert "Result: **0/1**" in md
    assert "Wilson 95%" in md


# ========================================================================================
# Group 9 — publication lifecycle
# ========================================================================================
def _minimal_session(session_id="20260101T000000Z-p4c"):
    return {
        "schema": redteam_live.SESSION_SCHEMA,
        "schema_version": redteam_live.SESSION_SCHEMA_VERSION,
        "run": {"run_id": session_id, "run_type": redteam_live.RUN_TYPE},
        "session": {
            "started_utc": "2026-01-01T00:00:00Z",
            "ended_utc": "2026-01-01T00:00:01Z",
            "terminal_state": redteam_live.TERMINAL_OPERATIONAL_EXHAUSTED,
            "valid_attempts": 0,
            "invalid_attempts": 1,
            "replacements_used": 1,
            "attempts_made": 1,
            "target_valid_attempts": 3,
            "max_replacements": 2,
            "max_attempts": 5,
        },
        "preflight": {"corpus_hash": "sha256:a", "promoted_rules_hash": "sha256:b"},
        "provider": {
            "model": "m",
            "temperature": 0.0,
            "max_tokens": 1024,
            "provider_attempt_timeout_s": 60.0,
            "sdk_max_retries": 0,
            "wrapper_backoff_schedule_s": [1.0, 4.0],
            "wrapper_jitter_cap_s": 1.0,
            "logical_call_upper_bound_s": 187.0,
            "triage_prompt_sha256": "sha256:c",
            "synthesis_prompt_sha256": "sha256:d",
            "coverage_catalog_prompt_sha256": "sha256:e",
            "timeout_scope_note": "note",
            "seed_note": "no seed",
        },
        "cost": {
            "actual_calls": 0,
            "calls_by_stage": {},
            "retries_by_stage": {},
            "max_calls_per_case": 6,
            "max_calls_per_clean_attempt": 180,
            "session_call_ceiling": 600,
            "ceiling_note": "cost-stop policy",
        },
        "attempts": [
            {
                "index": 1,
                "type": "operationally_invalid",
                "cause": "system_contamination",
                "started_utc": "2026-01-01T00:00:00Z",
                "ended_utc": "2026-01-01T00:00:01Z",
                "cases": [],
                "contaminated_case_ids": ["rt_01"],
                "failing_stages": ["ai_triage"],
                "error_kinds": ["timeout"],
                "not_attempted_case_ids": [],
            }
        ],
        "limits": ["a limit"],
    }


def test_a_published_session_is_exactly_two_regular_files(tmp_path):
    final = redteam_live.publish_session(_minimal_session(), out_dir=tmp_path / "root")
    assert redteam_live._is_valid_session_directory(final) is True
    assert sorted(p.name for p in final.iterdir()) == sorted(
        [redteam_live.JSON_FILENAME, redteam_live.MD_FILENAME]
    )


def test_an_existing_session_directory_is_refused_not_reused(tmp_path):
    root = tmp_path / "root"
    first = redteam_live.publish_session(_minimal_session(), out_dir=root)
    before = (first / redteam_live.JSON_FILENAME).read_text(encoding="utf-8")
    with pytest.raises(redteam_live.SessionCollisionError):
        redteam_live.publish_session(_minimal_session(), out_dir=root)
    assert (first / redteam_live.JSON_FILENAME).read_text(encoding="utf-8") == before


def test_write_failure_cleans_up_and_propagates_the_original_error(tmp_path, monkeypatch):
    from pathlib import Path

    real_write_text = Path.write_text

    def _flaky(self, *args, **kwargs):
        if self.name == redteam_live.MD_FILENAME:
            raise OSError("simulated disk failure")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _flaky)
    root = tmp_path / "root"
    with pytest.raises(OSError, match="simulated disk failure"):
        redteam_live.publish_session(_minimal_session(), out_dir=root)
    assert not root.exists()


def test_cleanup_failure_raises_the_chained_error(tmp_path, monkeypatch):
    from pathlib import Path

    real_write_text = Path.write_text

    def _flaky(self, *args, **kwargs):
        if self.name == redteam_live.MD_FILENAME:
            raise OSError("simulated write failure")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _flaky)
    monkeypatch.setattr(
        redteam_live.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(OSError("no"))
    )
    with pytest.raises(redteam_live.SessionPublicationCleanupError) as excinfo:
        redteam_live.publish_session(_minimal_session(), out_dir=tmp_path / "root")
    assert isinstance(excinfo.value.original_error, OSError)
    assert isinstance(excinfo.value.cleanup_error, OSError)
    assert "manual investigation" in str(excinfo.value)


def test_markdown_is_written_last_as_the_completion_marker():
    import inspect

    source = inspect.getsource(redteam_live.publish_session)
    json_pos = source.index("(final / JSON_FILENAME)")
    md_pos = source.index("(final / MD_FILENAME)")
    assert json_pos < md_pos


# ========================================================================================
# Group 10 — isolation, boundaries, and no live calls offline
# ========================================================================================
def _module_source():
    import inspect

    return inspect.getsource(redteam_live)


def test_the_module_is_an_authorized_redteam_consumer():
    from tests.test_redteam_isolation import SOLE_CONSUMERS

    assert "eval/redteam_live.py" in SOLE_CONSUMERS


def test_only_the_frozen_contract_guard_is_imported_from_the_offline_record_surface():
    source = _module_source()
    assert "from harness.eval.redteam_record import validate_frozen_contract" in source
    for forbidden in (
        "build_admission_record",
        "render_markdown as",
        "redteam_record.publish",
        "RECORD_SCHEMA",
        "redteam_admission_record",
    ):
        assert forbidden not in source, f"must not reach into P4(b): {forbidden!r}"


def test_the_module_never_names_the_case_store():
    source = _module_source()
    assert "CaseStore" not in source
    assert "harness.store" not in source


def test_the_module_never_reaches_the_triage_tuning_surface():
    source = _module_source()
    for forbidden in ("triage_fpr", "triage_recall", "anomaly_corpus"):
        assert forbidden not in source


def test_the_session_never_mentions_another_corpus_surface(monkeypatch):
    session = _session(monkeypatch, ["valid", "valid", "valid"])
    dumped = json.dumps(session)
    for foreign in (
        "deterministic_holdout",
        "deterministic_offline",
        "redteam_admission_record",
    ):
        assert foreign not in dumped


def test_no_offline_path_constructs_a_provider_client(monkeypatch):
    """The only live path is the operator command with --confirm-live. Everything else,
    including a dry run, must never reach the provider-construction seam."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(redteam_live, "validate_frozen_contract", lambda: _fake_manifest())
    monkeypatch.setattr(
        redteam_live,
        "_build_provider_client",
        lambda: pytest.fail("offline paths must never construct a provider client"),
    )
    redteam_live.main([])


def test_no_test_in_this_module_is_key_gated():
    """P4(c) has no key-gated test by contract: provider calls are operator-initiated only.

    The needles are assembled at runtime so this self-scan cannot match its own source."""
    source = open(__file__, encoding="utf-8").read()
    conditional_skip = "skip" + "if"
    real_environment = "os." + "environ"
    assert conditional_skip not in source, "no test may skip on the presence of a key"
    assert real_environment not in source, (
        "no test may read the real environment; the key is only ever set or removed "
        "through monkeypatch, so behavior never depends on the operator's shell"
    )
    assert re.search(r"\bAnthropicClient\b", source) is None, (
        "no test may construct the real provider client"
    )

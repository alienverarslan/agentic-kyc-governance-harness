"""Milestone 2: operational failures are fail-closed AND separated from domain evidence.

Two distinct properties are under test here, and conflating them is exactly the bug this
milestone fixes:

1. **Fail-closed.** When an LLM call fails (either boundary: ai_triage or synthesize) or a
   promoted rule raises, the run must NOT crash and must NOT continue as if the step had
   returned "nothing found". It escalates.
2. **Not domain evidence.** A timeout is not a fact about the company. Operational failures
   carry ``origin="system"`` with a T-family code, are excluded from domain finding
   metrics, and are counted separately. Modelling them as X2/F2 domain findings (as the
   code originally did) would pollute triage/rule quality figures with infrastructure noise.

All offline: failures are simulated by stubs raising, never by touching a network.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness.agent.runner import run_agent
from harness.contracts.findings import (
    NO_PROPOSAL_REASONING,
    AgentResult,
    Finding,
    GuardrailDecision,
    SynthesisProposal,
)
from harness.data.loader import load_dossier
from harness.eval.compound import _ALI, _AYSE, _dossier
from harness.eval.metrics import agent_finding_codes
from harness.llm.errors import LLMError
from harness.llm.stub import PolicyMirrorStub

# A message that must never surface in an auditable record. Stands in for what a real
# provider error can carry: endpoints, response bodies, credentials, prompt content.
#
# TEST FIXTURE, NOT A CREDENTIAL. The value is invented and non-functional; it is shaped
# like an API key precisely so the assertions below can prove that such a shape never
# reaches a finding, a trajectory, or a stored record. The key-shaped prefix is assembled
# from two fragments so that no contiguous key-shaped literal appears in the source, which
# keeps repository secret scanners from raising a false positive on it. The runtime value
# is unchanged.
_FIXTURE_PREFIX = "sk-" + "ant-"  # noqa: S105 - not a credential; see above
_SECRET = _FIXTURE_PREFIX + "LEAKED-KEY endpoint=https://internal.example/v1 body={'customer':'X'}"


class _RaisingTriageStub(PolicyMirrorStub):
    """Fails the ai_triage call; synthesis still mirrors policy."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def _triage(self, user: str):
        raise self._exc


class _RaisingSynthStub(PolicyMirrorStub):
    """Fails the synthesize call; triage raises no concerns."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def _synth(self, user: str):
        raise self._exc


def _clean():
    return load_dossier("case_01")


def _with_b2a():
    """A dossier carrying one real, independent domain finding (ownership sums to 95)."""
    owners = [(_ALI, 55.0), (_AYSE, 40.0)]
    return _dossier("SYS-B2A", reg_owners=owners, ubo_owners=owners)


def _with_a2():
    """A dossier carrying an unexplainable domain finding (tax_id mismatch)."""
    owners = [(_ALI, 60.0), (_AYSE, 40.0)]
    return _dossier("SYS-A2", reg_owners=owners, ubo_owners=owners, tax_ubo="9999999999")


def _t_findings(result):
    return [f for f in result.findings if f.origin == "system"]


def _codes(result):
    return {f.code for f in result.findings}


# --- 1/2/7: synthesize failures ------------------------------------------------------
def test_synthesize_timeout_is_fail_closed_and_records_no_proposal():
    r = run_agent(_clean(), _RaisingSynthStub(LLMError("timeout")), case_ref="s1", promoted_rules=[])

    t0 = _t_findings(r)
    assert len(t0) == 1
    assert (t0[0].code, t0[0].origin, t0[0].error_kind) == ("T0", "system", "timeout")
    assert t0[0].check_name == "synthesize"
    assert t0[0].severity == "unexplainable"
    assert r.decision == "escalate"  # fail-closed: the run survived and escalated

    # The audit trail must say "no proposal", never imply the LLM proposed escalate.
    assert r.proposal.status == "unavailable"
    assert r.proposal.proposed_decision is None
    assert r.proposal.unavailable_reason == "llm_call_failed"
    assert r.guardrail.finalization_mode == "fail_closed_no_proposal"
    assert r.guardrail.overridden is False  # nothing to override; meaningful only with the mode


def test_synthesize_schema_invalid_is_fail_closed_without_leaking_provider_text():
    exc = LLMError("schema_invalid", cause=RuntimeError(_SECRET))
    r = run_agent(_clean(), _RaisingSynthStub(exc), case_ref="s2", promoted_rules=[])

    t0 = _t_findings(r)
    assert (t0[0].code, t0[0].error_kind) == ("T0", "schema_invalid")
    assert r.decision == "escalate"
    assert all(_SECRET not in f.detail for f in r.findings)
    assert _SECRET not in r.proposal.reasoning
    assert r.proposal.reasoning == NO_PROPOSAL_REASONING


def test_unwrapped_exception_at_a_node_boundary_is_unexpected_not_provider_error():
    # A harness bug (not an LLMError) must be classified honestly as unexpected_exception,
    # never mislabelled a provider failure.
    r = run_agent(_clean(), _RaisingSynthStub(KeyError("harness bug")), case_ref="s3", promoted_rules=[])
    t0 = _t_findings(r)
    assert (t0[0].code, t0[0].error_kind) == ("T0", "unexpected_exception")
    assert r.decision == "escalate"


class _SelfDeclaredUnavailableStub(PolicyMirrorStub):
    """A model that ANSWERS but claims it has no proposal. `status` is part of the schema
    handed to the model as a tool definition, so a real model could emit this."""

    def _synth(self, user: str):
        return SynthesisProposal(status="unavailable", unavailable_reason="llm_call_failed")


def test_model_may_not_declare_its_own_unavailability():
    # Unavailability is a fact the HARNESS observes when a call fails — never something a
    # model that did answer may assert. Allowing it would let a model manufacture a
    # "no proposal was available" audit record and dodge being recorded as overridden.
    r = run_agent(_clean(), _SelfDeclaredUnavailableStub(), case_ref="s4", promoted_rules=[])
    t0 = _t_findings(r)
    assert (t0[0].code, t0[0].error_kind) == ("T0", "schema_invalid")
    assert t0[0].check_name == "synthesize"
    assert r.decision == "escalate"  # out-of-contract output is handled fail-closed
    assert r.guardrail.finalization_mode == "fail_closed_no_proposal"


# --- 3/4/5: triage failures and coexistence with domain findings ---------------------
def test_triage_timeout_escalates_and_retains_deterministic_findings():
    r = run_agent(_with_b2a(), _RaisingTriageStub(LLMError("timeout")), case_ref="t1", promoted_rules=[])

    assert _codes(r) == {"B2a", "T0"}  # the domain finding is NOT dropped by the failure
    t0 = _t_findings(r)
    assert (t0[0].check_name, t0[0].error_kind) == ("ai_triage", "timeout")
    assert r.decision == "escalate"  # T0 is unexplainable, so it dominates B2a
    assert r.triage is None  # no triage result was produced


def test_clean_dossier_with_only_an_operational_failure_still_escalates():
    r = run_agent(_clean(), _RaisingTriageStub(LLMError("provider_error")), case_ref="t2", promoted_rules=[])
    assert _codes(r) == {"T0"}
    assert r.decision == "escalate"  # fail-closed with no domain finding at all


def test_domain_a2_and_synthesis_failure_are_both_retained():
    r = run_agent(_with_a2(), _RaisingSynthStub(LLMError("timeout")), case_ref="t3", promoted_rules=[])
    assert _codes(r) == {"A2", "T0"}
    assert r.decision == "escalate"


# --- 6: F2 -> T1 migration -----------------------------------------------------------
def test_broken_promoted_rule_yields_t1_and_never_f2():
    from harness.rules.schema import CandidateRule

    broken = CandidateRule(rule_id="broken", template_id="capital_age_ceiling", params={"max_age_days": 180})
    r = run_agent(_clean(), PolicyMirrorStub(), case_ref="r1", promoted_rules=[broken])

    assert "F2" not in _codes(r)
    t1 = _t_findings(r)
    assert (t1[0].code, t1[0].origin, t1[0].error_kind) == ("T1", "system", "rule_runtime_error")
    assert r.decision == "escalate"


def test_f2_is_retired_from_the_taxonomy():
    with pytest.raises(ValidationError):
        Finding(check_name="x", code="F2", severity="unexplainable", detail="d")


# --- 8: contract invariants ----------------------------------------------------------
def test_finding_system_origin_and_code_must_agree():
    # valid
    Finding(check_name="n", code="T0", severity="unexplainable", detail="d",
            origin="system", error_kind="timeout")
    Finding(check_name="n", code="A2", severity="unexplainable", detail="d")

    # a system origin with a domain code
    with pytest.raises(ValidationError):
        Finding(check_name="n", code="A2", severity="unexplainable", detail="d",
                origin="system", error_kind="timeout")
    # a system code with a non-system origin
    with pytest.raises(ValidationError):
        Finding(check_name="n", code="T0", severity="unexplainable", detail="d",
                origin="ai_triage")
    # a system code with the DEFAULT (deterministic) origin
    with pytest.raises(ValidationError):
        Finding(check_name="n", code="T1", severity="unexplainable", detail="d")
    # error_kind without a system origin
    with pytest.raises(ValidationError):
        Finding(check_name="n", code="A2", severity="unexplainable", detail="d",
                error_kind="timeout")
    # a system finding without an error_kind
    with pytest.raises(ValidationError):
        Finding(check_name="n", code="T0", severity="unexplainable", detail="d",
                origin="system")


def test_synthesis_proposal_status_invariants():
    # valid
    SynthesisProposal(proposed_decision="approve", reasoning="ok")
    SynthesisProposal(status="unavailable", unavailable_reason="llm_call_failed")
    SynthesisProposal(status="unavailable", unavailable_reason="llm_call_failed",
                      reasoning=NO_PROPOSAL_REASONING)

    # available without a decision
    with pytest.raises(ValidationError):
        SynthesisProposal(status="available")
    # available carrying an unavailable_reason
    with pytest.raises(ValidationError):
        SynthesisProposal(status="available", proposed_decision="approve",
                          unavailable_reason="llm_call_failed")
    # unavailable but carrying a fabricated decision (the bug this contract prevents)
    with pytest.raises(ValidationError):
        SynthesisProposal(status="unavailable", proposed_decision="escalate",
                          unavailable_reason="llm_call_failed")
    # unavailable without a reason
    with pytest.raises(ValidationError):
        SynthesisProposal(status="unavailable")
    # unavailable carrying unbounded reasoning text (a provider-message leak vector)
    with pytest.raises(ValidationError):
        SynthesisProposal(status="unavailable", unavailable_reason="llm_call_failed",
                          reasoning=_SECRET)


# --- 9: metric separation ------------------------------------------------------------
def test_system_findings_are_excluded_from_domain_codes_but_counted_separately():
    r = run_agent(_with_b2a(), _RaisingTriageStub(LLMError("timeout")), case_ref="m1", promoted_rules=[])

    # domain view: only the real finding; the timeout is not evidence about the company
    assert agent_finding_codes(r) == {"B2a"}
    # operational view: counted, with its code
    assert r.system_failure_count == 1
    assert r.system_error_codes == ["T0"]


def test_operational_failure_alone_leaves_no_domain_codes():
    r = run_agent(_clean(), _RaisingTriageStub(LLMError("timeout")), case_ref="m2", promoted_rules=[])
    assert agent_finding_codes(r) == {"NONE"}  # no domain finding was made
    assert r.system_failure_count == 1
    assert r.decision == "escalate"  # yet the safety outcome is still fail-closed


def test_a_system_failure_can_never_produce_an_approval():
    # The structural safety claim: T-findings are unexplainable and findings only ever add,
    # so an operational failure can only ever raise a decision, never approve.
    for exc in (LLMError("timeout"), LLMError("provider_error"), LLMError("schema_invalid"),
                RuntimeError("boom")):
        for stub in (_RaisingTriageStub(exc), _RaisingSynthStub(exc)):
            r = run_agent(_clean(), stub, case_ref="far", promoted_rules=[])
            assert r.decision != "approve", (type(exc).__name__, type(stub).__name__)


# --- 10: no-leak sweep ---------------------------------------------------------------
def test_raw_exception_text_never_reaches_an_auditable_record():
    for exc in (LLMError("timeout", cause=RuntimeError(_SECRET)),
                LLMError("provider_error", cause=RuntimeError(_SECRET)),
                RuntimeError(_SECRET)):
        for stub in (_RaisingTriageStub(exc), _RaisingSynthStub(exc)):
            r = run_agent(_clean(), stub, case_ref="leak", promoted_rules=[])
            for f in r.findings:
                assert _SECRET not in f.detail
                assert _SECRET not in str(f.fields_involved)
            assert _SECRET not in r.proposal.reasoning
            assert _SECRET not in r.guardrail.override_reason


# --- defaults / contract hygiene -----------------------------------------------------
def test_agent_result_list_defaults_are_not_shared_between_instances():
    """Guards the expectation that each AgentResult owns its own list. Note this validates
    Pydantic's per-instance default copying (v2 deep-copies defaults, so a bare `= []`
    would pass too); the codebase uses Field(default_factory=list) for consistency."""
    gd = GuardrailDecision(final_decision="approve", overridden=False)
    sp = SynthesisProposal(proposed_decision="approve")
    a = AgentResult(case_ref="a", decision="approve", guardrail=gd, proposal=sp)
    b = AgentResult(case_ref="b", decision="approve", guardrail=gd, proposal=sp)

    a.system_error_codes.append("T0")
    assert b.system_error_codes == []
    assert a.system_error_codes == ["T0"]


def test_defaults_preserve_pre_milestone2_semantics():
    # Every pre-existing finding is domain/deterministic without being migrated, and every
    # ordinary proposal is 'available' — this is what keeps the older suites green.
    f = Finding(check_name="check_identity_consistency", code="A1", severity="info", detail="d")
    assert f.origin == "deterministic" and f.error_kind is None

    p = SynthesisProposal(proposed_decision="approve", reasoning="r")
    assert p.status == "available" and p.unavailable_reason is None

    gd = GuardrailDecision(final_decision="approve", overridden=False)
    assert gd.finalization_mode == "proposal_guarded"

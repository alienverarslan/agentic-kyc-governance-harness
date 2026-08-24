"""Guardrail overrides in BOTH directions: too-lax and too-cautious."""

from harness.agent.guardrail import apply_guardrail, decision_for_severities
from harness.contracts.findings import Finding, SynthesisProposal


def _finding(code: str, severity: str) -> Finding:
    return Finding(check_name="t", code=code, severity=severity, detail="", fields_involved=[])


def test_policy_precedence():
    assert decision_for_severities([]) == "approve"
    assert decision_for_severities(["info"]) == "approve"
    assert decision_for_severities(["explainable"]) == "request_more_info"
    assert decision_for_severities(["explainable", "unexplainable"]) == "escalate"


def test_override_too_lax_unexplainable_but_llm_approves():
    # LLM proposes approve while an unexplainable finding (B1b) exists -> escalate.
    findings = [_finding("B1b", "unexplainable")]
    proposal = SynthesisProposal(proposed_decision="approve", reasoning="", key_findings=[])
    gd = apply_guardrail(findings, proposal)
    assert gd.final_decision == "escalate"
    assert gd.overridden is True
    assert gd.required_actions == ["open_case", "escalate"]
    assert "approve" in gd.override_reason


def test_override_too_cautious_explainable_but_llm_escalates():
    # Over-caution is ALSO an override: explainable-only findings, LLM proposes escalate
    # -> guardrail downgrades to request_more_info.
    findings = [_finding("C1a", "explainable")]
    proposal = SynthesisProposal(proposed_decision="escalate", reasoning="", key_findings=[])
    gd = apply_guardrail(findings, proposal)
    assert gd.final_decision == "request_more_info"
    assert gd.overridden is True
    assert gd.required_actions == ["open_case"]


def test_no_override_when_proposal_agrees():
    findings = [_finding("A1", "info")]
    proposal = SynthesisProposal(proposed_decision="approve", reasoning="", key_findings=[])
    gd = apply_guardrail(findings, proposal)
    assert gd.final_decision == "approve"
    assert gd.overridden is False
    assert gd.required_actions == []

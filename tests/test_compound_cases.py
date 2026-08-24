"""P3: the compound decision-algebra suite.

Every generated case the harness had until now isolated ONE taxonomy code, so the
guardrail's actual job — combining multiple findings (from different checks and from the
AI triage layer) into one decision via max-severity, dropping none — was never asserted
directly. These tests drive the explicit compound matrix in ``harness.eval.compound`` and
assert four axes per case: exact final decision, exact finding-code set (no finding
disappears, none is spuriously added), trajectory partial order, and override behavior.

Hermeticity: every case runs through ``run_compound_case``, which always passes
``promoted_rules=[]``. That is the existing dependency-injection point — no new registry
abstraction — and it fully bypasses the on-disk promoted-rule store. ``test_suite_is_
hermetic_against_promoted_rules`` verifies this discriminatingly (with a dossier that WOULD
trigger the promoted capital rule) and monkeypatches both disk-loader fallbacks to explode
if the ``[]`` path ever secretly reads them.

Language note: statements below are "verified under the tested fixtures/scenarios", not
"proven" in a universal sense — every claim here is scoped to the specific dossiers and
stubs each test drives.
"""

from __future__ import annotations

import pytest

from harness.agent.runner import run_agent
from harness.eval.compound import _ALI, _AYSE, CompoundCase, build_compound_cases, _dossier
from harness.llm.stub import (
    AdversarialStub,
    OvercautiousStub,
    PolicyMirrorStub,
    ScriptedTriageStub,
)

_CASES = build_compound_cases()
_CASE_IDS = [c.case_id for c in _CASES]

_EXPECTED_CHECKS = (
    "check_identity_consistency",
    "check_ownership_consistency",
    "check_authority_chain",
    "check_ubo_derivation",
    "check_completeness",
    "check_learned_rules",
)


def _stub_for(case: CompoundCase):
    """Instantiate the LLM stand-in a case declares. Triage-scripted and proposer-biased
    stubs are mutually exclusive across the matrix."""
    if case.triage_concerns:
        assert case.proposer == "policy_mirror", (case.case_id, "triage + biased proposer not mixed")
        return ScriptedTriageStub(case.triage_concerns)
    if case.proposer == "adversarial":
        return AdversarialStub()
    if case.proposer == "overcautious":
        return OvercautiousStub()
    return PolicyMirrorStub()


def run_compound_case(case: CompoundCase):
    """The single hermetic entry point: always injects an empty in-memory rule set, so the
    result cannot depend on the on-disk store, the working directory, or test order."""
    return run_agent(case.dossier, _stub_for(case), case_ref=case.case_id, promoted_rules=[])


def _codes(result) -> set[str]:
    return {f.code for f in result.findings}


def _assert_trajectory(result):
    traj = result.trajectory
    for node in _EXPECTED_CHECKS:
        assert node in traj, (node, traj)
    pos = {n: i for i, n in enumerate(traj)}
    assert pos["ai_triage"] < pos["synthesize"] < pos["guardrail"] < pos["act"], traj
    # every deterministic check runs before the AI triage layer and the synthesis step
    for node in _EXPECTED_CHECKS:
        assert pos[node] < pos["ai_triage"], (node, traj)


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_compound_case_decision_findings_and_trajectory(case: CompoundCase):
    result = run_compound_case(case)

    # 1. exact final decision
    assert result.decision == case.expected_decision, (case.case_id, result.decision)
    # 2. exact finding-code set: none disappears, none is spuriously added
    assert _codes(result) == case.expected_codes, (case.case_id, sorted(_codes(result)))
    # 3. trajectory partial order
    _assert_trajectory(result)
    # 4. override behavior stated by the fixture
    assert result.guardrail.overridden is case.expects_override, (case.case_id, result.guardrail.overridden)


@pytest.mark.parametrize(
    "case", [c for c in _CASES if c.category == "proposal_override"], ids=[c.case_id for c in _CASES if c.category == "proposal_override"]
)
def test_proposal_override_reason_is_explicit(case: CompoundCase):
    """For the proposal-override cases, the LLM proposal and the final decision must
    genuinely differ, and the guardrail must record why."""
    result = run_compound_case(case)
    assert result.proposal.proposed_decision != result.guardrail.final_decision
    assert result.guardrail.final_decision == case.expected_decision
    assert result.guardrail.override_reason.strip() != ""
    # the recorded reason names both the proposed and the enforced decision
    assert result.proposal.proposed_decision in result.guardrail.override_reason
    assert result.guardrail.final_decision in result.guardrail.override_reason


def test_no_unexpected_approval_in_compound_suite():
    """Suite-wide safety invariant (false_approval_rate = 0): no case whose ground-truth
    decision is non-approve may ever be approved. Cases that are legitimately expected to
    approve (the inert-finding control) are exempt by design — their exact decision is
    asserted by the parametrized test above, so nothing is left unchecked here."""
    for case in _CASES:
        if case.expected_decision == "approve":
            continue
        result = run_compound_case(case)
        assert result.decision != "approve", (case.case_id, "false approval")


def _audit_fingerprint(result) -> dict:
    """The CANONICALIZED SEMANTIC audit surface of a run, excluding the case ref.

    Inclusion rule: deterministic, structurally-derived fields are compared; free-text
    renderings are NOT. Specifically excluded are ``Finding.detail`` and
    ``SynthesisProposal.reasoning`` — both are rendered prose that may legitimately embed
    input/evidence ordering (and, with a live model, ``reasoning`` would never match
    verbatim). Requiring them to be byte-equal would conflate "semantically
    order-independent" with "identically rendered". ``override_reason`` IS included: it is
    produced by our own guardrail from ``sorted({severities})``, so it is canonical by
    construction rather than a raw evidence rendering.
    """
    gd = result.guardrail
    return {
        "decision": result.decision,
        "findings": sorted(
            (f.code, f.severity, tuple(sorted(f.fields_involved))) for f in result.findings
        ),
        "proposal_decision": result.proposal.proposed_decision,
        "proposal_key_findings": sorted(result.proposal.key_findings),
        "guardrail_final": gd.final_decision,
        "guardrail_overridden": gd.overridden,
        "guardrail_override_reason": gd.override_reason,
        "guardrail_required_actions": sorted(gd.required_actions),
        "trajectory": list(result.trajectory),
    }


def test_decision_is_order_independent():
    """Commutativity / order-independence: the SAME logical finding combination (A2 + B2a)
    must yield an identical CANONICAL SEMANTIC AUDIT FINGERPRINT — decision, finding
    code/severity/canonical fields, proposal, guardrail audit fields, and the semantic
    trajectory — regardless of the order of shareholders and declared-UBO entries. Raw
    document/evidence rendering is deliberately not compared (see ``_audit_fingerprint``).
    The guardrail is set+max, so this holds by construction; the test verifies it against a
    regression that would make ordering matter."""
    owners_fwd = [(_ALI, 55.0), (_AYSE, 40.0)]
    owners_rev = list(reversed(owners_fwd))  # also reverses declared_ubo (derived from ubo_owners)

    forward = _dossier("ORD-fwd", reg_owners=owners_fwd, ubo_owners=owners_fwd, tax_ubo="9999999999")
    reverse = _dossier("ORD-rev", reg_owners=owners_rev, ubo_owners=owners_rev, tax_ubo="9999999999")

    rf = run_agent(forward, PolicyMirrorStub(), case_ref="ORD-fwd", promoted_rules=[])
    rr = run_agent(reverse, PolicyMirrorStub(), case_ref="ORD-rev", promoted_rules=[])

    # decision + exact codes first (a readable failure), then the whole audit surface.
    assert rf.decision == rr.decision == "escalate"
    assert _codes(rf) == _codes(rr) == {"A2", "B2a"}
    assert _audit_fingerprint(rf) == _audit_fingerprint(rr)


def test_suite_is_hermetic_against_promoted_rules(monkeypatch):
    """Discriminating hermeticity: use a dossier that WOULD trigger the promoted
    capital_age rule (weeks old, capital > 75M) and independently carries a real B2a
    finding. Under promoted_rules=[] the learned rule must not fire (no F1/F2), and NO disk
    pathway may be consulted.

    Every promoted-rule disk read goes through a ``load_promoted_rules()`` default-fallback
    call — there are exactly two (the runner's, and coverage's for the triage prompt) and no
    env-var or module-level cache pathway (the compiled graph is cached, but rules travel
    per-call through state, never baked in). We patch BOTH fallbacks to explode; a green run
    under promoted_rules=[] therefore verifies the injection cannot be bypassed via the
    default path."""
    from datetime import date

    from harness.rules.schema import CandidateRule, evaluate_rule

    young_high_capital = _dossier(
        "HERMETIC-01",
        reg_owners=[(_ALI, 55.0), (_AYSE, 40.0)],  # B2a, an independent real finding
        ubo_owners=[(_ALI, 55.0), (_AYSE, 40.0)],
        incorporation=date(2023, 1, 1),  # 14 days before the registry document -> "weeks old"
        capital=90_000_000.0,  # above any sane capital ceiling
    )

    # The dossier really is capital-rule-capable: the promoted rule WOULD fire on it.
    capital_rule = CandidateRule(
        rule_id="capital-age-v1",
        template_id="capital_age_ceiling",
        params={"max_age_days": 90, "max_capital": 75_000_000},
    )
    assert evaluate_rule(capital_rule, young_high_capital) is not None

    # Explode if EITHER default-fallback disk loader is ever consulted under the []-injection.
    def _boom(*args, **kwargs):  # pragma: no cover - only runs on a hermeticity regression
        raise AssertionError("load_promoted_rules() must not be called when promoted_rules=[] is injected")

    monkeypatch.setattr("harness.agent.runner.load_promoted_rules", _boom)
    monkeypatch.setattr("harness.agent.coverage.load_promoted_rules", _boom)

    result = run_agent(young_high_capital, PolicyMirrorStub(), case_ref="HERMETIC-01", promoted_rules=[])

    assert not any(code.startswith("F") for code in _codes(result)), sorted(_codes(result))
    assert _codes(result) == {"B2a"}
    assert result.decision == "request_more_info"

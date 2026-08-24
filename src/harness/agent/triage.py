"""AI triage layer (Faz 2): open-world contradiction discovery, fail-closed.

This is where the LLM finally does genuine, flexible work — catching red flags that NO
deterministic check anticipates (e.g. "registered address is residential but the company
declares heavy manufacturing", "notary date falls on a public holiday"). The deterministic
checks cannot enumerate these; the LLM can.

SAFETY (asymmetric initiative / fail-closed): the triage layer can only ever RAISE
caution. Its concerns are converted to findings whose severity is clamped to
{explainable, unexplainable} and fed to the same max-severity guardrail. Because findings
only ever add and the guardrail takes the maximum severity, a triage concern can push a
decision UP (approve→request_more_info→escalate) but can NEVER pull it down to approve.
The LLM here has no path to authorizing an action on its own.
"""

from __future__ import annotations

import json

from harness.contracts.documents import Dossier
from harness.contracts.findings import Finding, NovelConcern, TriageResult

TRIAGE_SYSTEM = (
    "You are a TRIAGE layer on top of a DETERMINISTIC compliance engine for corporate KYC "
    "dossiers (Turkish trade-registry documents: a trade-registry record, a signature "
    "circular, and a UBO declaration). The deterministic engine is AUTHORITATIVE and its "
    "decisions on the areas it covers are SETTLED. Your ONLY job is to flag a CONCRETE, "
    "OBSERVABLE red flag that (a) is present in the dossier text and (b) falls entirely "
    "OUTSIDE the deterministic coverage listed below. If you cannot point to such a "
    "specific fact, return an EMPTY list. Most dossiers should yield ZERO concerns.\n\n"
    "Do NOT raise, re-litigate, or 'suggest verifying' any of the following — they are "
    "already covered or intentionally acceptable:\n"
    "- a shareholder below the 25% threshold not being a declared UBO (this is CORRECT);\n"
    "- a shareholder or UBO who is not an authorized signatory (normal; managers may sign);\n"
    "- the declarant being a valid signatory, or document-date ordering/gaps (date validity "
    "is already checked deterministically);\n"
    "- shared surnames, family co-ownership, or near-equal ownership splits;\n"
    "- the ABSENCE of any document or field beyond the three provided (ID copies, "
    "source-of-funds, business descriptions, PEP/sanctions evidence are OUT OF SCOPE, not "
    "red flags);\n"
    "- a sector being generally 'high-risk'.\n\n"
    "Legitimately raise a concern only for a concrete internal anomaly the checks cannot "
    "see, e.g.: an activity/address mismatch suggesting a shell or front (heavy industry at "
    "a residential apartment), a value wildly implausible for the stated activity, a facially "
    "suspicious ownership-path narrative (nominee/proxy), or a jurisdiction mismatch.\n\n"
    "Severity: use 'unexplainable' (escalate) ONLY when the dossier itself contains concrete "
    "evidence of a likely shell/front/fraud/sanctions issue a human must review before any "
    "reliance. Otherwise use 'explainable' (request clarification). When in doubt, prefer "
    "'explainable' or raise nothing. You never approve and never lower caution. Return a "
    "TriageResult (empty novel_concerns if nothing qualifies)."
)


def build_triage_user(dossier: Dossier, coverage_summary: str) -> str:
    """The triage prompt payload: the deterministic coverage + the dossier as JSON."""
    return json.dumps(
        {
            "deterministic_coverage": coverage_summary,
            "dossier": dossier.model_dump(mode="json"),
        },
        ensure_ascii=False,
        indent=2,
    )


def triage_findings(result: TriageResult) -> list[Finding]:
    """Convert triage concerns to findings, CLAMPING severity so the AI can only raise.

    Any severity that is not exactly 'unexplainable' becomes 'explainable' — the triage
    layer therefore cannot emit an 'info' (non-raising) finding and cannot influence the
    decision toward approve."""
    findings: list[Finding] = []
    for concern in result.novel_concerns:
        unexplainable = concern.severity.strip().lower() == "unexplainable"
        findings.append(
            Finding(
                check_name="ai_triage",
                code="X2" if unexplainable else "X1",
                severity="unexplainable" if unexplainable else "explainable",
                detail=concern.detail,
                fields_involved=list(concern.fields_involved),
                origin="ai_triage",
            )
        )
    return findings


def _coerce_concerns(items: list) -> list[NovelConcern]:
    """Helper for stubs/tests: accept dicts or (detail, severity) tuples."""
    out: list[NovelConcern] = []
    for it in items:
        if isinstance(it, NovelConcern):
            out.append(it)
        elif isinstance(it, dict):
            out.append(NovelConcern(**it))
        else:  # (detail, severity[, fields])
            detail, severity = it[0], it[1]
            fields = list(it[2]) if len(it) > 2 else []
            out.append(NovelConcern(detail=detail, severity=severity, fields_involved=fields))
    return out

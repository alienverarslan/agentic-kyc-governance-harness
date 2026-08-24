"""check_learned_rules (Faz 3): applies every currently-promoted rule to the dossier.

Structurally identical in shape to the other deterministic checks in ``harness.agent.checks``
— it always runs (like ``check_completeness``, it has no document precondition) and returns
a ``CheckResult``. With zero promoted rules (the default, until a human promotes one via
``harness.rules.store.promote_rule``) this is a pure no-op: empty findings, same as if the
node were not in the graph at all.

Fail-closed defense in depth: a promoted rule is guaranteed regression-free by the
validation gate at promotion time, but ``evaluate_rule`` is still wrapped per-rule so that
if one somehow raises at runtime (a bug the gate's corpus didn't happen to exercise), the
failure becomes a T1 (origin="system", unexplainable → escalate) finding rather than
silently vanishing or crashing the whole screening run for every dossier.

T1, not a domain code: a rule-evaluation exception is an OPERATIONAL system failure, not
evidence about the company, so it must not pollute domain finding metrics. (This code was
``F2`` when the rule loop first shipped; see docs/milestone2_design.md for the migration.)
``F1`` — a rule firing as its template intends — remains genuine domain evidence.
"""

from __future__ import annotations

import logging

from harness.contracts.documents import Dossier
from harness.contracts.findings import CheckResult, Finding
from harness.rules.schema import CandidateRule, evaluate_rule

logger = logging.getLogger(__name__)

_NAME = "check_learned_rules"


def check_learned_rules(dossier: Dossier, rules: list[CandidateRule]) -> CheckResult:
    findings: list[Finding] = []
    for rule in rules:
        try:
            finding = evaluate_rule(rule, dossier)
        except Exception as exc:  # noqa: BLE001 - a broken promoted rule must not vanish
            # Bounded metadata only at ERROR level; the traceback is DEBUG-only. The raw
            # exception text is deliberately NOT interpolated into the finding below: it is
            # an auditable record, and an exception message can carry unbounded content.
            logger.error(
                "system failure at %s",
                _NAME,
                extra={
                    "error_kind": "rule_runtime_error",
                    "exception_class": type(exc).__name__,
                    "node": _NAME,
                    "case_ref": dossier.dossier_id,
                },
            )
            logger.debug(
                "learned-rule traceback (rule_id=%s, case_ref=%s)",
                rule.rule_id,
                dossier.dossier_id,
                exc_info=exc,
            )
            findings.append(
                Finding(
                    check_name=_NAME,
                    code="T1",
                    severity="unexplainable",
                    detail=(
                        f"promoted rule {rule.rule_id!r} (template {rule.template_id!r}) "
                        "failed to evaluate; the case is escalated for human review"
                    ),
                    fields_involved=[],
                    origin="system",
                    error_kind="rule_runtime_error",
                )
            )
            continue
        if finding is not None:
            findings.append(finding)
    return CheckResult(check_name=_NAME, ran=True, findings=findings)

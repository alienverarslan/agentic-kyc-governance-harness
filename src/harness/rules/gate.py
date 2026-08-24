"""The validation gate (Faz 3, part 3): the one thing standing between a candidate rule
and promotion into the permanent deterministic rule set.

Gate semantics, deliberately the strictest reasonable definition of "breaks a known-good
case": a candidate rule PASSES iff it produces NO finding at all on any of the 8 read-only
seed fixtures or the 420 generated cases (14 taxonomy codes x 30, the same corpus slice-2
validated against). This mirrors ``finding_match`` elsewhere in this harness (right
decision for the RIGHT reason, i.e. exact code-set equality) rather than only checking
whether the final decision changes: even a rule that would leave the guardrail's decision
unchanged (e.g. firing quietly alongside an existing unexplainable finding) still means the
rule is not scoped to genuinely novel territory, and is rejected. None of the 428 known
cases involve a capital/incorporation-age mismatch, so a correctly-parameterized
``capital_age_ceiling`` rule passes by construction; a badly-parameterized one (too low a
capital ceiling, too generous an age window) will legitimately misfire on the generator's
clean bases and gets caught here, offline, before it ever reaches a human approver.

This is entirely offline: it runs the same deterministic ``evaluate_rule`` against dossiers
already on disk / produced by the (also-deterministic) generator. No LLM call is involved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.contracts.documents import Dossier
from harness.rules.schema import CandidateRule, TEMPLATE_REGISTRY, evaluate_rule, validate_params

# Which anomaly-corpus type (see harness.agent.anomaly_corpus) each template targets, for
# the gate's informational efficacy preview. Not required for pass/fail — Faz 3 step 5 is
# the real post-promotion recall re-measurement — but it lets a human approver see at a
# glance whether the candidate actually catches anything before spending an approval cycle.
TEMPLATE_TARGET_ANOMALY: dict[str, str] = {
    "capital_age_ceiling": "implausible_capital",
}


@dataclass
class GateResult:
    rule_id: str
    template_id: str
    passed: bool
    param_errors: list[str] = field(default_factory=list)
    n_checked: int = 0
    regressions: list[dict[str, str]] = field(default_factory=list)
    anomaly_preview: dict[str, Any] | None = None


def _known_dossiers(*, per_code: int, seed: int) -> list[tuple[str, Dossier]]:
    """The 8 seed fixtures + the generated corpus, as (case_id, dossier) pairs."""
    from harness.data.loader import list_seed_ids, load_dossier
    from harness.generate.synthetic import generate_cases

    out = [(cid, load_dossier(cid)) for cid in list_seed_ids()]
    out += [(gc.case_id, gc.case.dossier) for gc in generate_cases(per_code=per_code, seed=seed)]
    return out


def run_validation_gate(rule: CandidateRule, *, per_code: int = 30, seed: int = 42) -> GateResult:
    """Run the full non-regression gate for ``rule``. Deterministic and offline."""
    template = TEMPLATE_REGISTRY.get(rule.template_id)
    if template is None:
        return GateResult(
            rule_id=rule.rule_id,
            template_id=rule.template_id,
            passed=False,
            param_errors=[f"unknown template: {rule.template_id!r}"],
        )

    param_errors = validate_params(template, rule.params)
    if param_errors:
        return GateResult(
            rule_id=rule.rule_id,
            template_id=rule.template_id,
            passed=False,
            param_errors=param_errors,
        )

    dossiers = _known_dossiers(per_code=per_code, seed=seed)
    regressions: list[dict[str, str]] = []
    for case_id, dossier in dossiers:
        finding = evaluate_rule(rule, dossier)
        if finding is not None:
            regressions.append({"case_id": case_id, "code": finding.code, "detail": finding.detail})

    anomaly_preview: dict[str, Any] | None = None
    target_type = TEMPLATE_TARGET_ANOMALY.get(rule.template_id)
    if target_type is not None:
        from harness.agent.anomaly_corpus import build_anomaly_corpus

        corpus = [c for c in build_anomaly_corpus(seed=seed, per_type=3) if c.anomaly_type == target_type]
        hits = sum(1 for c in corpus if evaluate_rule(rule, c.dossier) is not None)
        anomaly_preview = {"anomaly_type": target_type, "caught": hits, "total": len(corpus)}

    return GateResult(
        rule_id=rule.rule_id,
        template_id=rule.template_id,
        passed=not regressions,
        n_checked=len(dossiers),
        regressions=regressions,
        anomaly_preview=anomaly_preview,
    )


def render(result: GateResult) -> str:
    L = ["=" * 66, f"VALIDATION GATE — rule {result.rule_id!r} ({result.template_id})", "=" * 66]
    if result.param_errors:
        L.append("REJECTED at param validation (before touching the corpus):")
        for e in result.param_errors:
            L.append(f"    - {e}")
        L.append("=" * 66)
        return "\n".join(L)

    L.append(f"checked against : {result.n_checked} known cases (8 seed fixtures + generated corpus)")
    L.append(f"regressions     : {len(result.regressions)}")
    if result.anomaly_preview:
        p = result.anomaly_preview
        L.append(f"efficacy preview: {p['caught']}/{p['total']} '{p['anomaly_type']}' anomaly cases caught")
    L.append("")
    L.append(f"RESULT: {'PASSED — eligible for human approval' if result.passed else 'REJECTED'}")
    if result.regressions:
        L.append("")
        L.append("regressions (fired on a case with no such label — REJECTED because of these):")
        for r in result.regressions[:20]:
            L.append(f"    [{r['case_id']}] {r['code']}: {r['detail']}")
        if len(result.regressions) > 20:
            L.append(f"    ... and {len(result.regressions) - 20} more")
    L.append("=" * 66)
    return "\n".join(L)

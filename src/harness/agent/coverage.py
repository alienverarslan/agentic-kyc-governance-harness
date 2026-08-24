"""Machine-readable coverage catalog for the deterministic checks (Faz 1).

Each deterministic check declares WHAT it covers, which documents it needs, and which
taxonomy codes it can emit. This turns the checks' scope into data the AI triage layer
can read, so the triage layer knows what is ALREADY handled deterministically and only
raises concerns that fall OUTSIDE this coverage.

Keeping the catalog explicit (rather than inferring it) also lets a test assert that the
catalog stays in sync with the codes the checks actually produce — if a check starts
emitting a new code without updating the catalog, the test fails.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness.rules.schema import TEMPLATE_REGISTRY, CandidateRule
from harness.rules.store import load_promoted_rules


@dataclass(frozen=True)
class CheckScope:
    node: str
    requires: tuple[str, ...]          # documents needed for the check to run
    emits: tuple[str, ...]             # taxonomy codes it can produce
    covers: str                        # human/AI-readable scope description


COVERAGE_CATALOG: tuple[CheckScope, ...] = (
    CheckScope(
        node="check_identity_consistency",
        requires=("any two of: registry, circular, ubo",),
        emits=("A1", "A2"),
        covers="legal name & tax id consistency across documents (after Turkish normalization)",
    ),
    CheckScope(
        node="check_ownership_consistency",
        requires=("registry", "ubo"),
        emits=("B1a", "B1b", "B2a", "B2b"),
        covers="shareholding structure & ownership percentages: registry vs UBO declaration",
    ),
    CheckScope(
        node="check_authority_chain",
        requires=("circular", "ubo"),
        emits=("C1a", "C1b", "C2"),
        covers="whether the UBO-declaration signer is an authorized signatory, and date validity",
    ),
    CheckScope(
        node="check_ubo_derivation",
        requires=("ubo",),
        emits=("D1a", "D1b"),
        covers="beneficial owners at/above the 25% threshold are declared, and the declaration is arithmetically valid",
    ),
    CheckScope(
        node="check_completeness",
        requires=(),
        emits=("E1", "E2"),
        covers="missing documents or empty mandatory fields",
    ),
)


def covered_codes() -> set[str]:
    """The set of taxonomy codes the deterministic layer as a whole covers."""
    return {code for scope in COVERAGE_CATALOG for code in scope.emits}


def learned_rule_codes(promoted_rules: list[CandidateRule]) -> set[str]:
    """Taxonomy codes covered right now by human-promoted Faz 3 rules (empty until the
    first promotion). Kept separate from ``covered_codes()`` — which describes only the
    fixed slice-1/2 checks and must stay independent of runtime/promotion state so its
    sync test with the generator's code list never depends on filesystem state."""
    codes: set[str] = set()
    for rule in promoted_rules:
        template = TEMPLATE_REGISTRY.get(rule.template_id)
        if template is not None:
            codes.update(template.emits)
    return codes


def catalog_summary(promoted_rules: list[CandidateRule] | None = None) -> str:
    """A compact, readable description of deterministic coverage for the triage prompt.

    ``promoted_rules`` defaults to whatever is currently promoted on disk; pass an
    explicit list (e.g. ``[]``) for a hermetic/test-time summary. A promoted rule's
    taxonomy code is appended as an extra "already covered" line, so the AI triage layer
    stops re-flagging a concern that has since been codified deterministically."""
    lines = ["Deterministic checks already cover the following (do NOT re-report these):"]
    for scope in COVERAGE_CATALOG:
        lines.append(f"- {scope.node} [{', '.join(scope.emits)}]: {scope.covers}")

    if promoted_rules is None:
        promoted_rules = load_promoted_rules()
    dyn_codes = learned_rule_codes(promoted_rules)
    if dyn_codes:
        lines.append(
            f"- check_learned_rules [{', '.join(sorted(dyn_codes))}]: human-approved, "
            f"gate-validated rules promoted from the Faz 3 learning loop "
            f"({len(promoted_rules)} currently active)"
        )
    return "\n".join(lines)

"""Declarative rule schema (Faz 3): the machinery a learned rule is built from.

Design commitment, mirroring the rest of the harness ("LLM proposes, deterministic code
disposes"): a ``RuleTemplate`` is vetted, version-controlled Python — it is registered once by
a developer, exactly like a check in ``harness.agent.checks``. A ``CandidateRule`` is the
ONLY thing an LLM-assisted proposer (Faz 3, part 2) may author, and it is nothing but a
``template_id`` plus a flat dict of numeric ``params``. There is no field through which a
proposer can supply code, a severity, or a taxonomy code — those are fixed by the template
author and are not proposer-settable. ``validate_params`` is the structural fence: every
param must be declared by the template and fall within its author-chosen numeric bounds,
so even a compromised or hallucinating proposer cannot construct a rule that does anything
other than instantiate one of the pre-approved templates with in-range numbers.

This module only defines the machinery and the registry; concrete templates are registered
in ``harness.rules.templates``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from pydantic import BaseModel, Field

from harness.contracts.documents import Dossier
from harness.contracts.findings import Finding, Severity, TaxonomyCode


@dataclass(frozen=True)
class ParamSpec:
    """Author-chosen bounds for one template parameter. Not proposer-settable."""

    minimum: float
    maximum: float
    kind: Literal["int", "float"] = "float"


@dataclass(frozen=True)
class RuleTemplate:
    """A vetted, version-controlled rule shape. Everything except ``params`` values is fixed
    by whoever registers the template — a proposer only ever fills in numbers."""

    template_id: str
    description: str
    param_specs: dict[str, ParamSpec]
    emits: tuple[TaxonomyCode, ...]
    severity: Severity
    evaluate: Callable[[Dossier, dict[str, float]], Finding | None]


TEMPLATE_REGISTRY: dict[str, RuleTemplate] = {}


def register_template(template: RuleTemplate) -> None:
    """Register a vetted template once. Re-registering the same id is a programming
    error (templates are a fixed, reviewed set, not something to redefine at runtime)."""
    if template.template_id in TEMPLATE_REGISTRY:
        raise ValueError(f"template already registered: {template.template_id!r}")
    TEMPLATE_REGISTRY[template.template_id] = template


class CandidateRule(BaseModel):
    """The ONLY object an LLM-assisted rule-proposer may author: a template id plus a
    flat dict of numeric parameters. There is no code field and no severity/taxonomy
    field — those belong to the template, not the proposer."""

    rule_id: str
    template_id: str
    params: dict[str, float] = Field(default_factory=dict)
    rationale: str = ""
    proposed_by: str = "unknown"


def validate_params(template: RuleTemplate, params: dict[str, float]) -> list[str]:
    """Structural validation only: every template param present, no extra params, every
    value numeric and within the template author's bounds. Returns an empty list iff
    ``params`` is a valid instantiation of ``template``."""
    errors: list[str] = []
    spec_keys = set(template.param_specs)
    given_keys = set(params)

    for missing in sorted(spec_keys - given_keys):
        errors.append(f"missing required parameter: {missing!r}")
    for extra in sorted(given_keys - spec_keys):
        errors.append(f"unknown parameter (not part of template {template.template_id!r}): {extra!r}")

    for key, spec in template.param_specs.items():
        if key not in params:
            continue
        value = params[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"parameter {key!r} must be numeric, got {value!r}")
            continue
        if spec.kind == "int" and float(value) != int(value):
            errors.append(f"parameter {key!r} must be an integer, got {value!r}")
        if value < spec.minimum or value > spec.maximum:
            errors.append(
                f"parameter {key!r}={value} is out of allowed bounds "
                f"[{spec.minimum}, {spec.maximum}]"
            )
    return errors


def evaluate_rule(rule: CandidateRule, dossier: Dossier) -> Finding | None:
    """Apply one candidate/promoted rule to one dossier.

    Raises ``ValueError`` for an unknown template or invalid params — callers that need
    a fail-closed, never-raise behavior over untrusted/promoted rules (the graph node)
    must catch this explicitly; the validation gate and tests want it to raise loudly."""
    template = TEMPLATE_REGISTRY.get(rule.template_id)
    if template is None:
        raise ValueError(f"unknown rule template: {rule.template_id!r}")
    errors = validate_params(template, rule.params)
    if errors:
        raise ValueError(f"candidate rule {rule.rule_id!r} has invalid params: {errors}")
    return template.evaluate(dossier, rule.params)

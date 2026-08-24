"""Concrete rule templates, registered into ``schema.TEMPLATE_REGISTRY`` on import.

First (and so far only) template: ``capital_age_ceiling``, the direct answer to the Faz 4
recall gap. Recall measurement found the AI triage layer catches categorical/semantic
out-of-coverage anomalies reliably (~100%) but misses a purely QUANTITATIVE one — share
capital wildly disproportionate to how young the company is — about two-thirds of the
time, because it requires numeric + sector reasoning rather than pattern matching. Rather
than pushing the probabilistic layer harder, this codifies the check deterministically:
a company younger than ``max_age_days`` declaring share capital above ``max_capital`` is
flagged for human clarification. Severity is fixed at ``explainable`` by this template
(not proposer-settable): capital size alone is a plausibility flag, not proof of anything,
matching the project's existing default of "explainable, not fraud-by-assumption" for
similar structural-but-not-damning findings (e.g. D1a).
"""

from __future__ import annotations

from harness.contracts.documents import Dossier
from harness.contracts.findings import Finding
from harness.rules.schema import ParamSpec, RuleTemplate, register_template


def _evaluate_capital_age_ceiling(dossier: Dossier, params: dict[str, float]) -> Finding | None:
    reg = dossier.registry
    if reg is None:
        return None

    age_days = (reg.document_date - reg.incorporation_date).days
    if age_days < 0:
        # Incorporation after the registry document is a different anomaly
        # (out of scope for this template); do not double-report it here.
        return None

    max_age_days = params["max_age_days"]
    max_capital = params["max_capital"]
    if age_days > max_age_days or reg.share_capital <= max_capital:
        return None

    return Finding(
        check_name="check_learned_rules",
        code="F1",
        severity="explainable",
        detail=(
            f"share_capital {reg.share_capital:,.0f} exceeds the learned plausibility "
            f"ceiling of {max_capital:,.0f} for a company only {age_days} days old "
            f"(<= {int(max_age_days)}-day threshold); a source-of-funds clarification "
            "should be requested"
        ),
        fields_involved=["share_capital", "incorporation_date", "document_date"],
    )


register_template(
    RuleTemplate(
        template_id="capital_age_ceiling",
        description=(
            "Flags a company younger than max_age_days declaring share_capital above "
            "max_capital — codifies the capital-vs-incorporation-age plausibility check "
            "that the AI triage layer only caught ~33% of the time."
        ),
        param_specs={
            # 1 week .. 5 years: the template's operative range for "young company".
            "max_age_days": ParamSpec(minimum=7, maximum=1825, kind="int"),
            # 10,000 .. 1,000,000,000 TRY: a sane floor/ceiling on the capital threshold.
            "max_capital": ParamSpec(minimum=10_000, maximum=1_000_000_000, kind="float"),
        },
        emits=("F1",),
        severity="explainable",
        evaluate=_evaluate_capital_age_ceiling,
    )
)

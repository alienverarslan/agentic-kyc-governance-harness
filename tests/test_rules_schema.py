"""Faz 3 (part 1): the declarative rule schema + the capital_age_ceiling template.

Covers the structural fence a proposer can never step outside of: every param must be
declared by the template and within its author-chosen bounds, and the only thing a
CandidateRule can carry is a template_id + numeric params — no code, no severity.
"""

from datetime import date, timedelta

import pytest

from harness.contracts.documents import Dossier, RegistryDoc, Shareholder
from harness.rules.schema import (
    TEMPLATE_REGISTRY,
    CandidateRule,
    ParamSpec,
    RuleTemplate,
    evaluate_rule,
    register_template,
    validate_params,
)


def _dossier_with_registry(*, incorporation_date: date, document_date: date, share_capital: float) -> Dossier:
    reg = RegistryDoc(
        document_date=document_date,
        legal_name="Test A.Ş.",
        tax_id="1111111111",
        incorporation_date=incorporation_date,
        registered_address="Test Mah., İstanbul",
        share_capital=share_capital,
        shareholders=[Shareholder(name="Ahmet Kaya", ownership_pct=100.0)],
        company_status="active",
    )
    return Dossier(dossier_id="TEST-1", registry=reg)


def test_capital_age_ceiling_registered_by_default():
    assert "capital_age_ceiling" in TEMPLATE_REGISTRY
    template = TEMPLATE_REGISTRY["capital_age_ceiling"]
    assert template.emits == ("F1",)
    assert template.severity == "explainable"


def test_registering_duplicate_template_id_raises():
    dummy = RuleTemplate(
        template_id="capital_age_ceiling",  # already registered
        description="dup",
        param_specs={},
        emits=("F1",),
        severity="explainable",
        evaluate=lambda dossier, params: None,
    )
    with pytest.raises(ValueError):
        register_template(dummy)


def test_validate_params_reports_missing_and_extra_and_out_of_bounds():
    template = TEMPLATE_REGISTRY["capital_age_ceiling"]

    assert validate_params(template, {"max_age_days": 180, "max_capital": 10_000_000}) == []

    errors = validate_params(template, {"max_age_days": 180})
    assert any("max_capital" in e for e in errors)

    errors = validate_params(template, {"max_age_days": 180, "max_capital": 10_000_000, "bogus": 1})
    assert any("bogus" in e for e in errors)

    errors = validate_params(template, {"max_age_days": 999999, "max_capital": 10_000_000})
    assert any("out of allowed bounds" in e for e in errors)

    errors = validate_params(template, {"max_age_days": "not-a-number", "max_capital": 10_000_000})
    assert any("must be numeric" in e for e in errors)


def test_evaluate_rule_raises_on_unknown_template():
    rule = CandidateRule(rule_id="bad", template_id="does_not_exist", params={})
    dossier = _dossier_with_registry(
        incorporation_date=date(2024, 1, 1), document_date=date(2024, 2, 1), share_capital=500_000
    )
    with pytest.raises(ValueError):
        evaluate_rule(rule, dossier)


def test_evaluate_rule_raises_on_invalid_params():
    rule = CandidateRule(rule_id="bad-params", template_id="capital_age_ceiling", params={"max_age_days": 180})
    dossier = _dossier_with_registry(
        incorporation_date=date(2024, 1, 1), document_date=date(2024, 2, 1), share_capital=500_000
    )
    with pytest.raises(ValueError):
        evaluate_rule(rule, dossier)


def test_capital_age_ceiling_fires_on_weeks_old_company_with_huge_capital():
    doc_date = date(2024, 6, 1)
    dossier = _dossier_with_registry(
        incorporation_date=doc_date - timedelta(days=30),
        document_date=doc_date,
        share_capital=75_000_000,
    )
    rule = CandidateRule(
        rule_id="capital-age-v1",
        template_id="capital_age_ceiling",
        params={"max_age_days": 180, "max_capital": 10_000_000},
    )
    finding = evaluate_rule(rule, dossier)
    assert finding is not None
    assert finding.code == "F1"
    assert finding.severity == "explainable"
    assert finding.check_name == "check_learned_rules"


def test_capital_age_ceiling_silent_on_established_company():
    doc_date = date(2024, 6, 1)
    dossier = _dossier_with_registry(
        incorporation_date=date(2015, 1, 1),  # ~9 years old
        document_date=doc_date,
        share_capital=2_500_000,  # within the generator's normal clean-base range
    )
    rule = CandidateRule(
        rule_id="capital-age-v1",
        template_id="capital_age_ceiling",
        params={"max_age_days": 180, "max_capital": 10_000_000},
    )
    assert evaluate_rule(rule, dossier) is None


def test_capital_age_ceiling_silent_on_young_but_modest_capital():
    doc_date = date(2024, 6, 1)
    dossier = _dossier_with_registry(
        incorporation_date=doc_date - timedelta(days=30),
        document_date=doc_date,
        share_capital=500_000,  # young company but a plausible amount
    )
    rule = CandidateRule(
        rule_id="capital-age-v1",
        template_id="capital_age_ceiling",
        params={"max_age_days": 180, "max_capital": 10_000_000},
    )
    assert evaluate_rule(rule, dossier) is None


def test_capital_age_ceiling_does_not_double_report_impossible_dates():
    # incorporation AFTER the registry document is a different anomaly (out of this
    # template's scope); it must not also produce an F1 here.
    doc_date = date(2024, 6, 1)
    dossier = _dossier_with_registry(
        incorporation_date=doc_date + timedelta(days=10),
        document_date=doc_date,
        share_capital=75_000_000,
    )
    rule = CandidateRule(
        rule_id="capital-age-v1",
        template_id="capital_age_ceiling",
        params={"max_age_days": 180, "max_capital": 10_000_000},
    )
    assert evaluate_rule(rule, dossier) is None


def test_capital_age_ceiling_no_registry_is_silent():
    dossier = Dossier(dossier_id="NO-REG")
    rule = CandidateRule(
        rule_id="capital-age-v1",
        template_id="capital_age_ceiling",
        params={"max_age_days": 180, "max_capital": 10_000_000},
    )
    assert evaluate_rule(rule, dossier) is None

"""Compound-case decision-algebra matrix (P3).

Every dossier the single-injector generator produces isolates exactly ONE taxonomy code.
That never exercises the guardrail's actual job: combining MULTIPLE findings — from
different checks, and from the AI triage layer — into one final decision via max-severity,
with the invariant that no finding is ever dropped. This module hand-authors a small,
explicit matrix of two- and three-finding cases (plus proposal-override cases) so that
combination behavior is asserted directly.

Design choices:
* We do NOT touch the single-injector generator. We reuse only its Turkish surface
  transforms (``tr_upper`` / ``ascii_degrade``) as a library, and otherwise build each
  dossier explicitly here so every fixture's expected (decision, finding-code set,
  trajectory, override) is auditable in one place.
* Cases are DATA. Which stub drives each case (policy-mirror / scripted-triage /
  adversarial / overcautious) is described declaratively; the test harness instantiates
  the actual client. This keeps this module free of any LLM import.
* Assertions elsewhere are on the finding-CODE set (exact equality), never on a
  finding's origin. When ``origin`` (milestone 2) lands with default "deterministic",
  these fixtures need no change — the design is forward-compatible by construction.

Category taxonomy (for reporting / showcase, per the P3 review):
* ``finding_composition``   — multiple findings combine through the guardrail's severity
  algebra (the guardrail's primary job).
* ``proposal_override``     — a single finding set plus a mis-calibrated LLM proposal; the
  guardrail overrides it in the correct direction (the guardrail's authority property).
* ``inert_finding_control`` — NOT a compound case. A single info finding, kept as a control
  showing an inert finding is retained without blocking approve.

NOTE (P3 review): a "two independent info findings -> approve" COMPOUND case is intentionally
absent. The taxonomy has exactly ONE info-severity code (A1, from check_identity); a second
independent info finding cannot be produced without fabricating a mock finding, which the
review explicitly forbids. CMP-11 therefore carries a single finding and makes NO compound
claim — it is labelled ``inert_finding_control`` and verifies only the part of the property
that real taxonomy supports: an inert finding is preserved and does not wrongly block
approval. The retained-but-inert behaviour of A1 *alongside other findings* is covered by
the genuinely compound CMP-04 / CMP-05.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from harness.contracts.documents import (
    Dossier,
    RegistryDoc,
    Shareholder,
    Signatory,
    SignatureCircular,
    UboDeclaration,
    UboEntry,
)
from harness.generate.synthetic import ascii_degrade, tr_upper

# --- fixed, consistent baseline values (a clean dossier -> approve, code NONE) --------
_TAX = "1234567890"
_LEGAL = "Örnek Makine Anonim Şirketi"
_HEAD = "Örnek Makine"  # core+sector, for the A1 surface-variant recipe
_ADDR = "Cumhuriyet Mah. Atatürk Cad. No:12, Çankaya, Ankara"
_NOTARY = "Ankara 5. Noterliği 2023/1234"
_PATH = "doğrudan pay sahipliği"

_REG_DATE = date(2023, 1, 15)
_CIRC_DATE = date(2023, 3, 1)
_UBO_DATE = date(2023, 9, 1)
_INCORP = date(2018, 1, 1)  # well-established: the capital_age rule never fires here
_CAPITAL = 1_000_000.0
_VALID_FAR = date(2026, 1, 1)  # authority covers the declaration
_VALID_EXPIRED = date(2023, 6, 1)  # expired before _UBO_DATE -> C2

_ALI = "Ali Yıldız"
_AYSE = "Ayşe Demir"
_CLEAN_OWNERS = [(_ALI, 60.0), (_AYSE, 40.0)]


def _legal_surfaces() -> tuple[str, str, str]:
    """Three surface spellings of the SAME entity (registry / circular / ubo), the exact
    recipe the generator's A1 injector uses, so they resolve to one entity after Turkish
    normalization -> an A1 (info) finding, never an identity conflict."""
    return (
        f"{_HEAD} Anonim Şirketi",
        f"{tr_upper(_HEAD)} A.Ş.",
        f"{ascii_degrade(_HEAD)} AS",
    )


def _declared(owners: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """The beneficial owners that must be declared: those at/above the 25% threshold."""
    return [(n, p) for n, p in owners if p + 0.001 >= 25.0]


def _dossier(
    dossier_id: str,
    *,
    reg_owners: list[tuple[str, float]],
    ubo_owners: list[tuple[str, float]],
    declared: list[tuple[str, float]] | None = None,
    declarant: str = _ALI,
    signatory: str = _ALI,
    signatory_valid: date | None = _VALID_FAR,
    tax_reg: str = _TAX,
    tax_circ: str = _TAX,
    tax_ubo: str = _TAX,
    legal_reg: str = _LEGAL,
    legal_circ: str = _LEGAL,
    legal_ubo: str = _LEGAL,
    incorporation: date = _INCORP,
    capital: float = _CAPITAL,
) -> Dossier:
    """Assemble a full three-document dossier from explicit parts."""
    if declared is None:
        declared = _declared(ubo_owners)
    registry = RegistryDoc(
        document_date=_REG_DATE,
        legal_name=legal_reg,
        tax_id=tax_reg,
        incorporation_date=incorporation,
        registered_address=_ADDR,
        share_capital=capital,
        shareholders=[Shareholder(name=n, ownership_pct=p) for n, p in reg_owners],
        company_status="active",
    )
    circular = SignatureCircular(
        document_date=_CIRC_DATE,
        legal_name=legal_circ,
        tax_id=tax_circ,
        authorized_signatories=[Signatory(name=signatory, authority_type="sole", valid_until=signatory_valid)],
        notary_reference=_NOTARY,
    )
    ubo = UboDeclaration(
        document_date=_UBO_DATE,
        legal_name=legal_ubo,
        tax_id=tax_ubo,
        shareholders=[Shareholder(name=n, ownership_pct=p) for n, p in ubo_owners],
        declared_ubo=[UboEntry(name=n, ultimate_ownership_pct=p, ownership_path=_PATH) for n, p in declared],
        declarant_name=declarant,
    )
    return Dossier(dossier_id=dossier_id, registry=registry, circular=circular, ubo=ubo)


@dataclass
class CompoundCase:
    case_id: str
    category: str  # "finding_composition" | "proposal_override" | "benign"
    dossier: Dossier
    expected_decision: str
    expected_codes: set[str]
    # stub configuration (mutually exclusive across the matrix):
    triage_concerns: list[tuple[str, str]] = field(default_factory=list)  # (detail, severity)
    proposer: str = "policy_mirror"  # | "adversarial" | "overcautious"
    expects_override: bool = False
    rationale: str = ""


# --- ownership shapes -----------------------------------------------------------------
_B2A_OWNERS = [(_ALI, 55.0), (_AYSE, 40.0)]  # sums to 95 -> B2a (explainable)
_D1B_DECLARED = [(_ALI, 70.0), (_AYSE, 60.0)]  # sums to 130 -> D1b (unexplainable)
_ALT_TAX = "9999999999"  # a mismatching tax_id in one document -> A2 (unexplainable)


def build_compound_cases() -> list[CompoundCase]:
    """The explicit compound-case matrix. Every entry states its own ground truth."""
    surf_reg, surf_circ, surf_ubo = _legal_surfaces()
    cases: list[CompoundCase] = [
        CompoundCase(
            case_id="CMP-01",
            category="finding_composition",
            dossier=_dossier("CMP-01", reg_owners=_B2A_OWNERS, ubo_owners=_B2A_OWNERS, signatory_valid=_VALID_EXPIRED),
            expected_decision="request_more_info",
            expected_codes={"B2a", "C2"},
            rationale="two explainable findings (ownership gap + expired authority) stay at request_more_info",
        ),
        CompoundCase(
            case_id="CMP-02",
            category="finding_composition",
            dossier=_dossier("CMP-02", reg_owners=_B2A_OWNERS, ubo_owners=_B2A_OWNERS, tax_ubo=_ALT_TAX),
            expected_decision="escalate",
            expected_codes={"A2", "B2a"},
            rationale="explainable + unexplainable -> max severity escalates",
        ),
        CompoundCase(
            case_id="CMP-03",
            category="finding_composition",
            dossier=_dossier("CMP-03", reg_owners=_CLEAN_OWNERS, ubo_owners=_CLEAN_OWNERS, declared=_D1B_DECLARED, tax_ubo=_ALT_TAX),
            expected_decision="escalate",
            expected_codes={"A2", "D1b"},
            rationale="two unexplainable findings both reported; decision escalates",
        ),
        CompoundCase(
            case_id="CMP-04",
            category="finding_composition",
            dossier=_dossier(
                "CMP-04", reg_owners=_B2A_OWNERS, ubo_owners=_B2A_OWNERS, signatory_valid=_VALID_EXPIRED,
                legal_reg=surf_reg, legal_circ=surf_circ, legal_ubo=surf_ubo,
            ),
            expected_decision="request_more_info",
            expected_codes={"A1", "B2a", "C2"},
            rationale="info (A1) is retained but inert; explainables drive request_more_info",
        ),
        CompoundCase(
            case_id="CMP-05",
            category="finding_composition",
            dossier=_dossier(
                "CMP-05", reg_owners=_B2A_OWNERS, ubo_owners=_B2A_OWNERS, tax_ubo=_ALT_TAX,
                legal_reg=surf_reg, legal_circ=surf_circ, legal_ubo=surf_ubo,
            ),
            expected_decision="escalate",
            expected_codes={"A1", "A2", "B2a"},
            rationale="info + explainable + unexplainable; info inert, decision escalates",
        ),
        CompoundCase(
            case_id="CMP-06",
            category="finding_composition",
            dossier=_dossier("CMP-06", reg_owners=_B2A_OWNERS, ubo_owners=_B2A_OWNERS),
            expected_decision="request_more_info",
            expected_codes={"B2a", "X1"},
            triage_concerns=[("registered address looks residential for the declared heavy activity", "explainable")],
            rationale="a deterministic explainable finding plus an AI triage X1 concern",
        ),
        CompoundCase(
            case_id="CMP-07",
            category="finding_composition",
            dossier=_dossier("CMP-07", reg_owners=_CLEAN_OWNERS, ubo_owners=_CLEAN_OWNERS),
            expected_decision="request_more_info",
            expected_codes={"X1"},
            triage_concerns=[("an out-of-coverage ownership-path narrative", "explainable")],
            expects_override=True,
            rationale=(
                "asymmetric authority: an AI concern alone raises a clean case to "
                "request_more_info. The synthesis proposer cannot see the triage channel "
                "(triage findings are not in check_results), so it proposes approve and the "
                "guardrail overrides UP — the fail-closed property in action"
            ),
        ),
        CompoundCase(
            case_id="CMP-08",
            category="finding_composition",
            dossier=_dossier("CMP-08", reg_owners=_CLEAN_OWNERS, ubo_owners=_CLEAN_OWNERS),
            expected_decision="escalate",
            expected_codes={"X2"},
            triage_concerns=[("declared owner appears on a sanctions list", "unexplainable")],
            expects_override=True,
            rationale=(
                "an AI unexplainable concern alone escalates a clean case; the proposer "
                "(blind to the triage channel) proposes approve and is overridden to escalate"
            ),
        ),
        CompoundCase(
            case_id="CMP-09",
            category="proposal_override",
            dossier=_dossier("CMP-09", reg_owners=_CLEAN_OWNERS, ubo_owners=_CLEAN_OWNERS, tax_ubo=_ALT_TAX),
            expected_decision="escalate",
            expected_codes={"A2"},
            proposer="adversarial",
            expects_override=True,
            rationale="a lax proposer (always approve) over an unexplainable finding is overridden UP to escalate",
        ),
        CompoundCase(
            case_id="CMP-10",
            category="proposal_override",
            dossier=_dossier("CMP-10", reg_owners=_B2A_OWNERS, ubo_owners=_B2A_OWNERS),
            expected_decision="request_more_info",
            expected_codes={"B2a"},
            proposer="overcautious",
            expects_override=True,
            rationale="an overcautious proposer (always escalate) over an explainable finding is overridden DOWN to request_more_info",
        ),
        CompoundCase(
            case_id="CMP-11",
            category="inert_finding_control",
            dossier=_dossier(
                "CMP-11", reg_owners=_CLEAN_OWNERS, ubo_owners=_CLEAN_OWNERS,
                legal_reg=surf_reg, legal_circ=surf_circ, legal_ubo=surf_ubo,
            ),
            expected_decision="approve",
            expected_codes={"A1"},
            rationale=(
                "CONTROL, not a compound case: a SINGLE inert (info) finding is retained and "
                "does not block approval. No compound claim is made — the taxonomy has only one "
                "info code, so a two-info compound case cannot be built honestly (see module note)"
            ),
        ),
    ]
    return cases

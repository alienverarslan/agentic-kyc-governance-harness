"""Parametric synthetic dossier generator.

Strategy: build a fully-consistent CLEAN BASE (every check passes → approve), then apply
exactly one code-specific injector that perturbs a single aspect to trigger exactly one
taxonomy code while re-establishing consistency everywhere else. Because the generator
builds the case, it computes the ground truth directly (no human labeling). The harness
then self-validates every case (see ``generate.selfcheck``): the agent's finding codes
must equal the injected code exactly, or the recipe is buggy.

Tiers:
* isolation  — randomized clean base + single-code injection (the 8-seed style, at scale).
* boundary   — near-threshold variants that stress branch logic (25.0 vs 24.9%, 365 vs
               366 days, valid_until ±1 day, arithmetic that just (fails to) reconcile).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, timedelta
from random import Random

from harness.contracts.documents import (
    Dossier,
    RegistryDoc,
    Shareholder,
    SignatureCircular,
    Signatory,
    UboDeclaration,
    UboEntry,
)
from harness.contracts.truth import Case, DecisionTruth
from harness.generate import pools
from harness.normalize.config import AUTHORITY_STALENESS_DAYS, UBO_THRESHOLD_PCT

# Every code the checks can emit, plus NONE. This is the generator's coverage target.
ALL_CODES: list[str] = [
    "NONE", "A1", "A2", "B1a", "B1b", "B2a", "B2b",
    "C1a", "C1b", "C2", "D1a", "D1b", "E1", "E2",
]

DECISION_FOR_CODE: dict[str, str] = {
    "NONE": "approve",
    "A1": "approve",
    "A2": "escalate",
    "B1a": "request_more_info",
    "B1b": "escalate",
    "B2a": "request_more_info",
    "B2b": "escalate",
    "C1a": "request_more_info",
    "C1b": "escalate",
    "C2": "request_more_info",
    "D1a": "request_more_info",
    "D1b": "escalate",
    "E1": "request_more_info",
    "E2": "request_more_info",
}

# Codes carrying an unexplainable finding → these are the "adversarial" set: an LLM that
# proposes approve on them must be overridden to escalate by the guardrail.
ESCALATE_CODES = {c for c, d in DECISION_FOR_CODE.items() if d == "escalate"}

CHECK_ORDER = [
    "check_identity_consistency",
    "check_ownership_consistency",
    "check_authority_chain",
    "check_ubo_derivation",
    "check_completeness",
]


# --------------------------------------------------------------------------------------
# Turkish surface transforms (for A1 spelling variants)
# --------------------------------------------------------------------------------------
_ASCII = str.maketrans(
    {
        "İ": "I", "I": "I", "ı": "i", "Ş": "S", "ş": "s", "Ğ": "G", "ğ": "g",
        "Ü": "U", "ü": "u", "Ö": "O", "ö": "o", "Ç": "C", "ç": "c",
    }
)


def tr_upper(text: str) -> str:
    """Turkish-correct uppercasing (i→İ, ı→I) for ALL-CAPS spelling variants."""
    return text.replace("i", "İ").replace("ı", "I").upper()


def ascii_degrade(text: str) -> str:
    """Drop Turkish diacritics to ASCII (the 'degraded scan' spelling variant)."""
    return text.translate(_ASCII)


# --------------------------------------------------------------------------------------
# Blueprint: a mutable, pre-model description of a dossier + which documents are present
# --------------------------------------------------------------------------------------
@dataclass
class Party:
    name: str
    pct: float


@dataclass
class Sig:
    name: str
    authority_type: str
    valid_until: date | None


@dataclass
class Blueprint:
    dossier_id: str
    tax_id: str
    legal_reg: str
    legal_circ: str
    legal_ubo: str
    incorporation_date: date
    address: str
    share_capital: float
    company_status: str
    reg_date: date
    circ_date: date
    ubo_date: date
    reg_owners: list[Party]
    ubo_owners: list[Party]
    declared: list[Party]
    signatories: list[Sig]
    declarant: str
    notary_reference: str
    tax_id_reg: str = ""
    tax_id_circ: str = ""
    tax_id_ubo: str = ""
    has_reg: bool = True
    has_circ: bool = True
    has_ubo: bool = True
    blank: str | None = None  # None | "tax_id" | "legal_name"

    def clone(self) -> "Blueprint":
        return copy.deepcopy(self)

    # -- rendering to contract models --
    def _tax(self, which: str) -> str:
        if self.blank == "tax_id":
            return ""
        return {"reg": self.tax_id_reg, "circ": self.tax_id_circ, "ubo": self.tax_id_ubo}[which]

    def _legal(self, surface: str) -> str:
        return "" if self.blank == "legal_name" else surface

    def to_dossier(self) -> Dossier:
        registry = circular = ubo = None
        if self.has_reg:
            registry = RegistryDoc(
                document_date=self.reg_date,
                legal_name=self._legal(self.legal_reg),
                tax_id=self._tax("reg"),
                incorporation_date=self.incorporation_date,
                registered_address=self.address,
                share_capital=self.share_capital,
                shareholders=[Shareholder(name=p.name, ownership_pct=p.pct) for p in self.reg_owners],
                company_status=self.company_status,  # type: ignore[arg-type]
            )
        if self.has_circ:
            circular = SignatureCircular(
                document_date=self.circ_date,
                legal_name=self._legal(self.legal_circ),
                tax_id=self._tax("circ"),
                authorized_signatories=[
                    Signatory(name=s.name, authority_type=s.authority_type, valid_until=s.valid_until)  # type: ignore[arg-type]
                    for s in self.signatories
                ],
                notary_reference=self.notary_reference,
            )
        if self.has_ubo:
            ubo = UboDeclaration(
                document_date=self.ubo_date,
                legal_name=self._legal(self.legal_ubo),
                tax_id=self._tax("ubo"),
                shareholders=[Shareholder(name=p.name, ownership_pct=p.pct) for p in self.ubo_owners],
                declared_ubo=[
                    UboEntry(name=p.name, ultimate_ownership_pct=p.pct, ownership_path=pools.OWNERSHIP_PATH_TR)
                    for p in self.declared
                ],
                declarant_name=self.declarant,
            )
        return Dossier(dossier_id=self.dossier_id, registry=registry, circular=circular, ubo=ubo)


def expected_flow(has_reg: bool, has_circ: bool, has_ubo: bool) -> tuple[list[str], list[str]]:
    """The trajectory + skipped set the graph will produce for the given documents.

    Mirrors the check preconditions in ``harness.agent.graph``. This logic is independently
    anchored by the curated seed fixtures (e.g. seed #8), so using it to label generated
    E1 cases does not make the generator grade its own homework on trajectory."""
    present = sum([has_reg, has_circ, has_ubo])
    runs = {
        "check_identity_consistency": present >= 2,
        "check_ownership_consistency": has_reg and has_ubo,
        "check_authority_chain": has_circ and has_ubo,
        "check_ubo_derivation": has_ubo,
        "check_completeness": True,
    }
    traj = ["extract", "resolve_entities"] + [c for c in CHECK_ORDER if runs[c]] + [
        "synthesize", "guardrail", "act",
    ]
    skipped = [c for c in CHECK_ORDER if not runs[c]]
    return traj, skipped


@dataclass
class GeneratedCase:
    case_id: str
    code: str
    tier: str
    case: Case = field(repr=False)


# --------------------------------------------------------------------------------------
# Random building blocks
# --------------------------------------------------------------------------------------
def _person(rng: Random) -> str:
    return f"{rng.choice(pools.FIRST_NAMES)} {rng.choice(pools.SURNAMES)}"


def _distinct_people(rng: Random, n: int) -> list[str]:
    people: list[str] = []
    while len(people) < n:
        p = _person(rng)
        if p not in people:
            people.append(p)
    return people


def _tax_id(rng: Random) -> str:
    return "".join(rng.choice("0123456789") for _ in range(10))


def _ownership(rng: Random, n: int) -> list[tuple[str, float]]:
    """(shape) pct splits summing to 100. Some include a sub-25% holder (like seed #2)."""
    if n == 2:
        a = rng.choice([50.0, 55.0, 60.0, 65.0, 70.0])
        return [("o0", a), ("o1", 100.0 - a)]
    shapes = [
        [45.0, 35.0, 20.0], [50.0, 30.0, 20.0], [40.0, 35.0, 25.0],
        [34.0, 33.0, 33.0], [50.0, 25.0, 25.0], [45.0, 30.0, 25.0],
    ]
    return [(f"o{i}", p) for i, p in enumerate(rng.choice(shapes))]


def _declared(owners: list[Party]) -> list[Party]:
    """The beneficial owners that MUST be declared: those at/above the threshold."""
    return [Party(o.name, o.pct) for o in owners if o.pct + 0.001 >= UBO_THRESHOLD_PCT]


def clean_base(rng: Random, dossier_id: str) -> Blueprint:
    """A fully-consistent dossier: identity, ownership, authority, UBO, completeness all
    clean → decision approve, code NONE."""
    n = rng.choice([2, 3])
    people = _distinct_people(rng, n)
    splits = _ownership(rng, n)
    owners = [Party(people[i], pct) for i, (_slot, pct) in enumerate(splits)]

    core = rng.choice(pools.COMPANY_CORES)
    sector = rng.choice(pools.SECTORS)
    suffix_full, _caps, _ascii = rng.choice(pools.LEGAL_SUFFIXES)
    legal = f"{core} {sector} {suffix_full}"

    province, district = rng.choice(pools.CITIES)
    address = f"{rng.choice(pools.STREETS)} No:{rng.randint(1, 200)}, {district}, {province}"

    base_year = rng.randint(2023, 2024)
    reg_date = date(base_year, rng.randint(1, 6), rng.randint(1, 28))
    circ_date = reg_date + timedelta(days=rng.randint(20, 120))
    ubo_date = circ_date + timedelta(days=rng.randint(30, 200))
    valid_until = ubo_date + timedelta(days=rng.randint(700, 1400))
    incorporation = date(base_year - rng.randint(3, 12), rng.randint(1, 12), rng.randint(1, 28))

    # Declarant is a signatory (authority clean). One or two signatories.
    n_sig = 1 if n == 2 else rng.choice([1, 2])
    sig_owners = owners[:n_sig]
    auth = "sole" if n_sig == 1 else "joint"
    signatories = [Sig(o.name, auth, valid_until) for o in sig_owners]
    declarant = sig_owners[0].name

    return Blueprint(
        dossier_id=dossier_id,
        tax_id="",
        legal_reg=legal,
        legal_circ=legal,
        legal_ubo=legal,
        incorporation_date=incorporation,
        address=address,
        share_capital=float(rng.choice([500000, 750000, 1000000, 1500000, 2500000])),
        company_status="active",
        reg_date=reg_date,
        circ_date=circ_date,
        ubo_date=ubo_date,
        reg_owners=[Party(o.name, o.pct) for o in owners],
        ubo_owners=[Party(o.name, o.pct) for o in owners],
        declared=_declared(owners),
        signatories=signatories,
        declarant=declarant,
        notary_reference=f"{province} {rng.randint(1, 40)}. Noterliği {base_year}/{rng.randint(1000, 99999)}",
        tax_id_reg=(t := _tax_id(rng)),
        tax_id_circ=t,
        tax_id_ubo=t,
    )


# --------------------------------------------------------------------------------------
# Injectors — each triggers exactly one code
# --------------------------------------------------------------------------------------
def _inject_A1(bp: Blueprint, rng: Random) -> str:
    """Same entity, three surface spellings (proper / ALL-CAPS Turkish / ASCII)."""
    core_sector = bp.legal_reg.rsplit(" ", 3)  # split off the 3-token suffix, best-effort
    # Recompute cleanly from parts instead of parsing: rebuild from stored pieces.
    # legal_reg == "<core> <sector> <full suffix>"; find the matching suffix triple.
    for full, caps, asc in pools.LEGAL_SUFFIXES:
        if bp.legal_reg.endswith(full):
            head = bp.legal_reg[: -len(full)].strip()
            bp.legal_circ = f"{tr_upper(head)} {caps}"
            bp.legal_ubo = f"{ascii_degrade(head)} {asc}"
            break
    # ubo document spells people in ASCII-degraded form; they must still resolve.
    bp.ubo_owners = [Party(ascii_degrade(p.name), p.pct) for p in bp.ubo_owners]
    bp.declared = [Party(ascii_degrade(p.name), p.pct) for p in bp.declared]
    bp.declarant = ascii_degrade(bp.declarant)
    _ = core_sector
    return "isolation"


def _inject_A2(bp: Blueprint, rng: Random) -> str:
    """One document carries a different tax_id."""
    alt = _tax_id(rng)
    while alt == bp.tax_id_reg:
        alt = _tax_id(rng)
    bp.tax_id_ubo = alt
    return "isolation"


def _transfer(bp: Blueprint) -> None:
    """Make ubo a newer view with one holder exited, pct absorbed by the first holder."""
    owners = bp.reg_owners
    if len(owners) < 2:
        # force a 3-owner base upstream; fall back by splitting the smaller holder
        return
    exiting = owners[-1]
    new_owners = [Party(o.name, o.pct) for o in owners[:-1]]
    new_owners[0] = Party(new_owners[0].name, new_owners[0].pct + exiting.pct)
    bp.ubo_owners = new_owners
    bp.declared = _declared(new_owners)


def _inject_B1a(bp: Blueprint, rng: Random) -> str:
    """Explainable transfer: ubo newer, overlap, arithmetic reconciles."""
    _transfer(bp)  # ubo_date > reg_date already holds in the base
    bp.declarant = bp.ubo_owners[0].name
    bp.signatories = [Sig(bp.ubo_owners[0].name, "sole", bp.ubo_date + timedelta(days=900))]
    return "isolation"


def _inject_B1b(bp: Blueprint, rng: Random) -> str:
    """Unexplainable: ubo OLDER than registry with a different structure (fails cond i)."""
    _transfer(bp)
    bp.ubo_date = bp.reg_date - timedelta(days=rng.randint(200, 900))
    bp.declarant = bp.ubo_owners[0].name
    bp.signatories = [Sig(bp.ubo_owners[0].name, "sole", bp.reg_date + timedelta(days=1200))]
    return "isolation"


def _inject_B2a(bp: Blueprint, rng: Random) -> str:
    """Both lists sum below 100 (gap closable by one record)."""
    drop = rng.choice([3.0, 5.0, 8.0])
    bp.reg_owners[0].pct -= drop
    bp.ubo_owners[0].pct -= drop
    bp.declared = _declared(bp.ubo_owners)
    return "isolation"


def _inject_B2b(bp: Blueprint, rng: Random) -> str:
    """Both lists sum above 100 (structurally impossible).

    declared_ubo is snapshotted from the still-valid (pre-inflation) owners so it lists
    every at/above-threshold owner at percentages summing ≤ 100 — no spurious D1a (a
    required owner is undeclared) or D1b (declared sum > 100)."""
    bp.declared = _declared(bp.ubo_owners)  # valid: all ≥threshold owners, sum ≤ 100
    add = rng.choice([5.0, 10.0, 15.0])
    bp.reg_owners[0].pct += add
    bp.ubo_owners[0].pct += add
    return "isolation"


def _new_person_not_in(rng: Random, existing: list[str]) -> str:
    p = _person(rng)
    while p in existing:
        p = _person(rng)
    return p


def _inject_C1a(bp: Blueprint, rng: Random) -> str:
    """Declarant absent from circular; circular materially older than the declaration."""
    gap = AUTHORITY_STALENESS_DAYS + rng.randint(30, 500)
    bp.circ_date = bp.ubo_date - timedelta(days=gap)
    bp.reg_date = bp.circ_date - timedelta(days=rng.randint(10, 60))
    bp.incorporation_date = date(bp.reg_date.year - 4, 1, 1)
    bp.declarant = _new_person_not_in(rng, [s.name for s in bp.signatories])
    return "isolation"


def _inject_C1b(bp: Blueprint, rng: Random) -> str:
    """Declarant absent; circular contemporaneous-or-newer than the declaration."""
    bp.circ_date = bp.ubo_date + timedelta(days=rng.randint(10, 200))
    for s in bp.signatories:
        s.valid_until = bp.circ_date + timedelta(days=900)
    bp.declarant = _new_person_not_in(rng, [s.name for s in bp.signatories])
    return "isolation"


def _inject_C2(bp: Blueprint, rng: Random) -> str:
    """Declarant is a signatory but their authority expired before the declaration."""
    expired = bp.ubo_date - timedelta(days=rng.randint(10, 400))
    bp.signatories[0] = Sig(bp.declarant, bp.signatories[0].authority_type, expired)
    return "isolation"


def _inject_D1a(bp: Blueprint, rng: Random) -> str:
    """An at/above-threshold shareholder is missing from declared_ubo."""
    above = [p for p in bp.ubo_owners if p.pct + 0.001 >= UBO_THRESHOLD_PCT]
    if len(above) < 2:  # ensure a second ≥threshold owner exists to drop
        bp.ubo_owners = [Party(bp.ubo_owners[0].name, 55.0), Party(_person(rng), 45.0)]
        bp.reg_owners = [Party(p.name, p.pct) for p in bp.ubo_owners]
        above = list(bp.ubo_owners)
    bp.declared = _declared(bp.ubo_owners)[:-1]  # omit one required owner
    return "isolation"


def _inject_D1b(bp: Blueprint, rng: Random) -> str:
    """Declared ultimate holdings are structurally impossible (sum > 100)."""
    names = [p.name for p in _declared(bp.ubo_owners)]
    if len(names) < 2:
        names = [bp.ubo_owners[0].name, _person(rng)]
    bp.declared = [Party(names[0], 70.0), Party(names[1], 60.0)]  # sums to 130
    return "isolation"


def _inject_E1(bp: Blueprint, rng: Random) -> str:
    """A required document is missing (downstream checks skipped)."""
    which = rng.choice(["registry", "circular", "ubo"])
    setattr(bp, {"registry": "has_reg", "circular": "has_circ", "ubo": "has_ubo"}[which], False)
    return "isolation"


def _inject_E2(bp: Blueprint, rng: Random) -> str:
    """A mandatory field is empty across all documents (consistent blank → only E2)."""
    bp.blank = rng.choice(["tax_id", "legal_name"])
    return "isolation"


INJECTORS = {
    "A1": _inject_A1, "A2": _inject_A2,
    "B1a": _inject_B1a, "B1b": _inject_B1b, "B2a": _inject_B2a, "B2b": _inject_B2b,
    "C1a": _inject_C1a, "C1b": _inject_C1b, "C2": _inject_C2,
    "D1a": _inject_D1a, "D1b": _inject_D1b,
    "E1": _inject_E1, "E2": _inject_E2,
}


# --------------------------------------------------------------------------------------
# Boundary injectors — near-threshold variants for codes with a numeric edge
# --------------------------------------------------------------------------------------
def _boundary_C1a(bp: Blueprint, rng: Random) -> str:
    """Circular exactly AUTHORITY_STALENESS_DAYS+1 older → still C1a (just over the edge)."""
    bp.circ_date = bp.ubo_date - timedelta(days=AUTHORITY_STALENESS_DAYS + 1)
    bp.reg_date = bp.circ_date - timedelta(days=10)
    bp.incorporation_date = date(bp.reg_date.year - 4, 1, 1)
    bp.declarant = _new_person_not_in(rng, [s.name for s in bp.signatories])
    return "boundary"


def _boundary_C1b(bp: Blueprint, rng: Random) -> str:
    """Circular exactly AUTHORITY_STALENESS_DAYS older → NOT materially older → C1b."""
    bp.circ_date = bp.ubo_date - timedelta(days=AUTHORITY_STALENESS_DAYS)
    bp.reg_date = bp.circ_date - timedelta(days=10)
    bp.incorporation_date = date(bp.reg_date.year - 4, 1, 1)
    for s in bp.signatories:
        s.valid_until = bp.ubo_date + timedelta(days=900)
    bp.declarant = _new_person_not_in(rng, [s.name for s in bp.signatories])
    return "boundary"


def _boundary_C2(bp: Blueprint, rng: Random) -> str:
    """valid_until exactly one day before the declaration → expired → C2."""
    bp.signatories[0] = Sig(bp.declarant, bp.signatories[0].authority_type, bp.ubo_date - timedelta(days=1))
    return "boundary"


def _boundary_NONE_threshold(bp: Blueprint, rng: Random) -> str:
    """A sub-threshold (24.9%) holder is legitimately undeclared → stays clean (NONE)."""
    lead = Party(bp.ubo_owners[0].name, 75.1)
    small = Party(_person(rng), 24.9)
    bp.reg_owners = [lead, Party(small.name, small.pct)]
    bp.ubo_owners = [Party(lead.name, lead.pct), Party(small.name, small.pct)]
    bp.declared = [Party(lead.name, lead.pct)]  # only the 75.1% owner; 24.9% not required
    bp.declarant = lead.name
    bp.signatories = [Sig(lead.name, "sole", bp.ubo_date + timedelta(days=900))]
    return "boundary"


def _boundary_D1a_threshold(bp: Blueprint, rng: Random) -> str:
    """A holder at exactly 25.0% is required but omitted → D1a (the edge that must flag)."""
    lead = Party(bp.ubo_owners[0].name, 75.0)
    edge = Party(_person(rng), 25.0)
    bp.reg_owners = [lead, edge]
    bp.ubo_owners = [Party(lead.name, lead.pct), Party(edge.name, edge.pct)]
    bp.declared = [Party(lead.name, lead.pct)]  # 25.0% owner omitted
    bp.declarant = lead.name
    bp.signatories = [Sig(lead.name, "sole", bp.ubo_date + timedelta(days=900))]
    return "boundary"


# Boundary variants keyed by the code they should still resolve to.
BOUNDARY = {
    "C1a": _boundary_C1a,
    "C1b": _boundary_C1b,
    "C2": _boundary_C2,
    "NONE": _boundary_NONE_threshold,
    "D1a": _boundary_D1a_threshold,
}


# --------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------
def _build_case(bp: Blueprint, code: str) -> Case:
    traj, skipped = expected_flow(bp.has_reg, bp.has_circ, bp.has_ubo)
    truth = DecisionTruth(
        expected_decision=DECISION_FOR_CODE[code],  # type: ignore[arg-type]
        expected_trajectory=traj,
        expected_skipped_checks=skipped,
        injected_codes=[code],
        rationale=f"synthetic {code}",
    )
    return Case(dossier=bp.to_dossier(), decision_truth=truth)


def generate_for_code(code: str, n: int, rng: Random, *, boundary_fraction: float = 0.25) -> list[GeneratedCase]:
    """Generate ``n`` cases for one taxonomy code (mixing in boundary variants if any)."""
    out: list[GeneratedCase] = []
    n_boundary = int(n * boundary_fraction) if code in BOUNDARY else 0
    for i in range(n):
        bp = clean_base(rng, dossier_id=f"GEN-{code}-{i:03d}")
        if code == "NONE" and i >= n - n_boundary:
            tier = BOUNDARY["NONE"](bp, rng)
        elif code != "NONE" and i >= n - n_boundary:
            tier = BOUNDARY[code](bp, rng)
        elif code == "NONE":
            tier = "isolation"
        else:
            tier = INJECTORS[code](bp, rng)
        out.append(GeneratedCase(case_id=bp.dossier_id, code=code, tier=tier, case=_build_case(bp, code)))
    return out


def generate_cases(per_code: int = 30, seed: int = 42, codes: list[str] | None = None) -> list[GeneratedCase]:
    """Generate ``per_code`` cases for every taxonomy code, deterministically from ``seed``."""
    rng = Random(seed)
    cases: list[GeneratedCase] = []
    for code in (codes or ALL_CODES):
        cases.extend(generate_for_code(code, per_code, rng))
    return cases

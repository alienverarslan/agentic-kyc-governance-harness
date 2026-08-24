"""A labeled corpus of DETERMINISTICALLY-CLEAN dossiers that each hide one out-of-coverage
red flag the AI triage layer *should* catch (Faz 4, recall side).

Every case here passes all deterministic checks (decision would be 'approve'), so the ONLY
component that can catch the planted anomaly is the AI triage layer. Measuring how many it
catches gives recall / miss-rate — the complement to the false-positive rate.

The anomalies are all things the deterministic checks structurally cannot see (they read
names, tax ids, percentages, signatories, dates-for-validity, document presence — not
address semantics, capital plausibility, company status, or free-text ownership paths).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from random import Random

from harness.contracts.documents import Dossier
from harness.generate.synthetic import clean_base

_RESIDENTIAL = [
    "Bahçelievler Mah. Papatya Sok. No:3 Daire 5, Çankaya, Ankara",
    "Kızılay Mah. Menekşe Sok. No:12 Daire 8, Çankaya, Ankara",
    "Caferağa Mah. Gül Sok. No:7 Daire 4, Kadıköy, İstanbul",
]
_HEAVY = [
    ("Demir Döküm Ağır Sanayi A.Ş.", "heavy industry / iron casting"),
    ("Deniz Nakliyat Denizcilik A.Ş.", "maritime shipping"),
    ("Anadolu Maden İşletme Sanayi A.Ş.", "mining operations"),
]


@dataclass
class AnomalyCase:
    anomaly_type: str
    detail: str
    dossier: Dossier
    # Explicit recall ground-truth label (P5). Every case built by ``_BUILDERS`` plants
    # exactly one anomaly the triage layer is expected to catch, so this is True for the
    # whole corpus today — but making it an explicit field (rather than an inferred
    # convention) means a future negative/control case added to this corpus cannot
    # silently corrupt the recall denominator in ``triage_recall.py``.
    expected_triage_concern: bool = True


def _address_activity(rng: Random, i: int) -> AnomalyCase:
    bp = clean_base(rng, f"ANOM-addr-{i:02d}")
    name, activity = rng.choice(_HEAVY)
    bp.legal_reg = bp.legal_circ = bp.legal_ubo = name
    bp.address = rng.choice(_RESIDENTIAL)
    return AnomalyCase("address_activity_mismatch", f"{activity} @ residential apartment", bp.to_dossier())


def _implausible_capital(rng: Random, i: int) -> AnomalyCase:
    bp = clean_base(rng, f"ANOM-cap-{i:02d}")
    bp.incorporation_date = bp.reg_date - timedelta(days=rng.randint(15, 45))  # weeks old
    bp.share_capital = float(rng.choice([75_000_000, 90_000_000, 120_000_000]))
    return AnomalyCase("implausible_capital", "weeks-old SME with 75M+ capital", bp.to_dossier())


def _nominee_path(rng: Random, i: int) -> AnomalyCase:
    bp = clean_base(rng, f"ANOM-nom-{i:02d}")
    d = bp.to_dossier()
    if d.ubo is not None:
        for e in d.ubo.declared_ubo:
            e.ownership_path = "vekâleten üçüncü kişi (nominee) üzerinden dolaylı pay sahipliği"
    return AnomalyCase("nominee_ownership_path", "declared UBOs held via undisclosed nominee", d)


def _liquidation_status(rng: Random, i: int) -> AnomalyCase:
    bp = clean_base(rng, f"ANOM-liq-{i:02d}")
    bp.company_status = "liquidation"  # yet a fresh UBO declaration / normal-looking dossier
    return AnomalyCase("liquidation_but_active_filing", "company in liquidation filing fresh UBO", bp.to_dossier())


def _incorporation_after_registry(rng: Random, i: int) -> AnomalyCase:
    bp = clean_base(rng, f"ANOM-date-{i:02d}")
    bp.incorporation_date = bp.reg_date + timedelta(days=rng.randint(30, 150))  # impossible
    return AnomalyCase("incorporation_after_registry", "incorporation date AFTER the registry document", bp.to_dossier())


def _jurisdiction_mismatch(rng: Random, i: int) -> AnomalyCase:
    bp = clean_base(rng, f"ANOM-juris-{i:02d}")
    far = rng.choice(["Hakkari", "Ardahan", "Şırnak", "Iğdır"])
    bp.notary_reference = f"{far} 1. Noterliği {bp.circ_date.year}/{rng.randint(100, 9999)}"
    return AnomalyCase("notary_jurisdiction_mismatch", f"notary in {far}, far from registered address", bp.to_dossier())


_BUILDERS = [
    _address_activity,
    _implausible_capital,
    _nominee_path,
    _liquidation_status,
    _incorporation_after_registry,
    _jurisdiction_mismatch,
]


def build_anomaly_corpus(seed: int = 42, per_type: int = 3) -> list[AnomalyCase]:
    """``per_type`` variants of each anomaly type, deterministically from ``seed``."""
    rng = Random(seed)
    corpus: list[AnomalyCase] = []
    for builder in _BUILDERS:
        for i in range(per_type):
            corpus.append(builder(rng, i))
    return corpus

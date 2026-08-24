"""Authority check: C1a/C1b staleness boundary, C2 expiry, normalized membership.

The date-direction logic must SEPARATE seed #6 (C1a) from seed #7 (C1b). No seed case
exists for C2 — this unit test is its only coverage.
"""

from harness.agent.checks import build_entity_map, check_authority_chain
from harness.contracts.documents import Dossier
from tests.builders import circular, ubo


def _emap(circ, u):
    return build_entity_map(Dossier(dossier_id="T", circular=circ, ubo=u))


def _run(circ, u):
    return check_authority_chain(circ, u, _emap(circ, u))


def _codes(result):
    return [f.code for f in result.findings]


def test_c1a_circular_materially_older():
    # Declarant absent; circular > 365 days older than declaration -> explainable.
    circ = circular([("Mehmet Aydın", "sole", "2026-09-12")], "2023-09-12")
    u = ubo([("Pelin Arslan", 55), ("Deniz Arslan", 45)], "2025-10-05", "Elif Şahin")
    result = _run(circ, u)
    assert _codes(result) == ["C1a"]
    assert result.findings[0].severity == "explainable"


def test_c1b_circular_newer_than_declaration():
    # Declarant absent; circular NEWER than declaration -> temporal explanation closed.
    circ = circular([("Serkan Karaca", "joint", "2028-12-01"), ("Hülya Karaca", "joint", "2028-12-01")], "2025-12-01")
    u = ubo([("Serkan Karaca", 65), ("Hülya Karaca", 35)], "2025-06-15", "Burak Çelik")
    result = _run(circ, u)
    assert _codes(result) == ["C1b"]
    assert result.findings[0].severity == "unexplainable"


def test_c1a_c1b_boundary_at_365_days():
    # Exactly 365 days is NOT "materially older" (needs > 365) -> C1b.
    circ_365 = circular([("X", "sole", None)], "2024-01-01")
    u = ubo([("A", 100)], "2024-12-31", "Declarant Yok")  # 365 days later
    assert _codes(_run(circ_365, u)) == ["C1b"]
    # 366 days crosses the threshold -> C1a.
    u2 = ubo([("A", 100)], "2025-01-01", "Declarant Yok")  # 366 days later
    assert _codes(_run(circ_365, u2)) == ["C1a"]


def test_c2_declarant_authority_expired():
    # No seed case: unit test is the only coverage. Declarant is a signatory but their
    # authority lapsed before the declaration date.
    circ = circular([("Ahmet Kaya", "sole", "2024-01-01")], "2023-01-01")
    u = ubo([("Ahmet Kaya", 100)], "2025-01-01", "Ahmet Kaya")
    result = _run(circ, u)
    assert _codes(result) == ["C2"]
    assert result.findings[0].severity == "explainable"


def test_normalized_membership_no_finding():
    # Declarant matches a signatory only AFTER Turkish-aware normalization (seed #3).
    circ = circular([("İLKER IŞIK", "sole", "2027-11-02")], "2024-11-02")
    u = ubo([("Ilker Isik", 70), ("Sila Isik", 30)], "2025-04-17", "Ilker Isik")
    result = _run(circ, u)
    assert result.findings == []


def test_non_shareholder_signatory_is_not_a_finding():
    # Seed #6: the circular's signatory is a professional manager (non-shareholder).
    # As long as the declarant matches (or the temporal branch handles absence), the
    # signatory not being a shareholder is never itself a finding.
    circ = circular([("Mehmet Aydın", "sole", "2026-09-12")], "2025-09-12")
    u = ubo([("Pelin Arslan", 55)], "2025-10-05", "Mehmet Aydın")
    result = _run(circ, u)
    assert result.findings == []

"""Ownership check: the three-condition explainability test + the sum branches.

The three-condition test is the logic that must SEPARATE seed #4 (B1a) from seed #5
(B1b). There is one test per failing condition (i)/(ii)/(iii). No seed case exists for
B2a/B2b — these unit tests are their ONLY coverage.
"""

from harness.agent.checks import build_entity_map, check_ownership_consistency
from harness.contracts.documents import Dossier
from tests.builders import registry, ubo


def _emap(reg, u):
    return build_entity_map(Dossier(dossier_id="T", registry=reg, ubo=u))


def _run(reg, u):
    return check_ownership_consistency(reg, u, _emap(reg, u))


def _codes(result):
    return [f.code for f in result.findings]


def test_b1a_all_three_conditions_pass():
    # Seed #4 shape: B3 newer, overlap, exited 20% absorbed by a continuing holder.
    reg = registry([("Ahmet Kaya", 50), ("Zeynep Kaya", 30), ("Murat Öztürk", 20)], "2024-02-27")
    u = ubo([("Ahmet Kaya", 50), ("Zeynep Kaya", 50)], "2025-11-10", "Ahmet Kaya")
    result = _run(reg, u)
    assert _codes(result) == ["B1a"]
    assert result.findings[0].severity == "explainable"


def test_b1b_fails_condition_i_date_direction():
    # B3 OLDER than registry (seed #5's decisive failure) -> B1b even with overlap.
    reg = registry([("Ali Demir", 60), ("Ayşe Demir", 40)], "2025-09-01")
    u = ubo([("Ali Demir", 40), ("Ayşe Demir", 30), ("Kemal Aslan", 30)], "2023-05-20", "Ali Demir")
    result = _run(reg, u)
    assert _codes(result) == ["B1b"]
    assert result.findings[0].severity == "unexplainable"


def test_b1b_fails_condition_ii_no_overlap():
    # B3 newer and arithmetic balances, but zero continuing shareholders -> B1b.
    reg = registry([("Ahmet Kaya", 60), ("Zeynep Kaya", 40)], "2024-01-01")
    u = ubo([("Cem Yıldız", 60), ("Deniz Ak", 40)], "2025-01-01", "Cem Yıldız")
    result = _run(reg, u)
    assert _codes(result) == ["B1b"]


def test_b1b_fails_condition_iii_arithmetic():
    # B3 newer, overlap present, but exited total != gained by continuing/new -> B1b.
    reg = registry([("Ahmet Kaya", 50), ("Zeynep Kaya", 30), ("Murat Öztürk", 20)], "2024-01-01")
    u = ubo([("Ahmet Kaya", 40), ("Zeynep Kaya", 60)], "2025-01-01", "Ahmet Kaya")
    result = _run(reg, u)
    assert _codes(result) == ["B1b"]


def test_b2a_sum_below_100():
    # No seed case: unit test is the only coverage. Gap closable by one missing record.
    reg = registry([("A", 50), ("B", 40)], "2024-01-01")  # sums to 90
    u = ubo([("A", 50), ("B", 40)], "2025-01-01", "A")
    result = _run(reg, u)
    assert _codes(result) == ["B2a"]
    assert result.findings[0].severity == "explainable"


def test_b2b_sum_above_100():
    # No seed case: unit test is the only coverage. Structurally impossible.
    reg = registry([("A", 60), ("B", 55)], "2024-01-01")  # sums to 115
    u = ubo([("A", 60), ("B", 40)], "2025-01-01", "A")
    result = _run(reg, u)
    assert _codes(result) == ["B2b"]
    assert result.findings[0].severity == "unexplainable"


def test_no_finding_when_structurally_identical_after_normalization():
    # Seed #3 shape: spellings differ but fold to the same people, pcts equal -> clean.
    reg = registry([("İlker Işık", 70), ("Sıla Işık", 30)], "2024-10-08")
    u = ubo([("Ilker Isik", 70), ("Sila Isik", 30)], "2025-04-17", "Ilker Isik")
    result = _run(reg, u)
    assert result.findings == []

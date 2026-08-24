"""check_ubo_derivation: threshold-aware UBO reconciliation (D1a / D1b).

The load-bearing constraint is seed #2: a shareholder BELOW the 25% threshold is
legitimately absent from declared_ubo and must NOT be flagged. These tests pin that,
plus the missing-owner (D1a) and structurally-impossible (D1b) branches, and a run of
the real seed #2 dossier as a regression.
"""

from harness.agent.checks import build_entity_map, check_ubo_derivation
from harness.agent.runner import run_agent
from harness.contracts.documents import Dossier
from harness.data.loader import load_case
from harness.llm.stub import PolicyMirrorStub
from tests.builders import ubo


def _run(u):
    emap = build_entity_map(Dossier(dossier_id="T", ubo=u))
    return check_ubo_derivation(u, emap)


def _codes(result):
    return [f.code for f in result.findings]


def test_below_threshold_shareholder_not_flagged():
    # Seed #2 shape: a 20% shareholder is intentionally not a declared UBO -> clean.
    u = ubo(
        [("Cemil Yıldız", 45), ("Nurten Yıldız", 35), ("Oğuz Kaan Erdem", 20)],
        "2025-09-14",
        "Nurten Yıldız",
        declared=[("Cemil Yıldız", 45), ("Nurten Yıldız", 35)],
    )
    assert _run(u).findings == []


def test_all_above_threshold_declared_is_clean():
    u = ubo(
        [("Ali Demir", 40), ("Ayşe Demir", 30), ("Kemal Aslan", 30)],
        "2024-01-01",
        "Ali Demir",
        declared=[("Ali Demir", 40), ("Ayşe Demir", 30), ("Kemal Aslan", 30)],
    )
    assert _run(u).findings == []


def test_d1a_missing_at_threshold_owner():
    # A 30% shareholder (>= 25%) is absent from declared_ubo -> D1a, explainable.
    u = ubo(
        [("Ahmet Kaya", 70), ("Zeynep Kaya", 30)],
        "2024-01-01",
        "Ahmet Kaya",
        declared=[("Ahmet Kaya", 70)],  # Zeynep missing
    )
    result = _run(u)
    assert _codes(result) == ["D1a"]
    assert result.findings[0].severity == "explainable"


def test_d1a_uses_normalized_names():
    # The declared UBO is the same person as the shareholder, only spelled differently
    # (Turkish folding) -> NOT a gap.
    u = ubo(
        [("İlker Işık", 70), ("Sıla Işık", 30)],
        "2025-04-17",
        "Ilker Isik",
        declared=[("Ilker Isik", 70), ("Sila Isik", 30)],
    )
    assert _run(u).findings == []


def test_d1b_structurally_impossible_sum():
    # Declared ultimate holdings sum to > 100% -> structurally impossible -> D1b.
    u = ubo(
        [("A", 60), ("B", 40)],
        "2024-01-01",
        "A",
        declared=[("A", 70), ("B", 60)],  # sums to 130
    )
    result = _run(u)
    assert _codes(result) == ["D1b"]
    assert result.findings[0].severity == "unexplainable"


def test_seed_02_regression_no_ubo_finding():
    # The real seed #2 must stay approve with no UBO finding once the node is live.
    case = load_case("case_02")
    result = run_agent(case.dossier, PolicyMirrorStub(), case_ref="case_02")
    ubo_result = next(c for c in result.check_results if c.check_name == "check_ubo_derivation")
    assert ubo_result.ran is True
    assert ubo_result.findings == []
    assert result.decision == "approve"
    assert not any(f.code.startswith("D") for f in result.findings)

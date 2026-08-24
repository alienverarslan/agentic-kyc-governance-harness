"""Faz 1: the coverage catalog must stay in sync with what the checks actually cover.

If a check gains/loses a taxonomy code without updating the catalog, or a catalog node
drifts from the graph's check nodes, these fail — keeping the AI triage layer's notion of
"already covered" honest.
"""

from harness.agent.coverage import COVERAGE_CATALOG, catalog_summary, covered_codes
from harness.eval.metrics import CHECK_NODES
from harness.generate.synthetic import ALL_CODES


def test_catalog_nodes_match_graph_check_nodes():
    assert {s.node for s in COVERAGE_CATALOG} == CHECK_NODES


def test_catalog_covers_exactly_the_deterministic_codes():
    # The deterministic codes are every taxonomy code the generator injects except NONE
    # (and excluding the AI-only X codes, which are not in ALL_CODES).
    assert covered_codes() == set(ALL_CODES) - {"NONE"}


def test_summary_mentions_every_check():
    summary = catalog_summary()
    for scope in COVERAGE_CATALOG:
        assert scope.node in summary

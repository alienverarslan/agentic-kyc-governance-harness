"""Ground truth must be physically separated from the dossier the agent sees.

The agent-facing loader path returns a ``Dossier`` that structurally cannot carry the
ground truth; only the eval path returns a ``Case``.
"""

from harness.contracts.documents import Dossier
from harness.contracts.truth import Case
from harness.data.loader import load_case, load_dossier


def test_dossier_model_has_no_truth_field():
    # The agent's input type simply has no place to put ground truth.
    assert "decision_truth" not in Dossier.model_fields
    assert "injected_codes" not in Dossier.model_fields
    assert "expected_decision" not in Dossier.model_fields


def test_load_dossier_returns_only_dossier():
    dossier = load_dossier("case_05")  # #5 is an escalate case; truth must not leak
    assert isinstance(dossier, Dossier)
    assert not hasattr(dossier, "decision_truth")
    # The serialized agent input contains no ground-truth keys.
    dumped = dossier.model_dump()
    assert "decision_truth" not in dumped
    assert "injected_codes" not in dumped


def test_eval_path_is_the_only_route_to_truth():
    case = load_case("case_05")
    assert isinstance(case, Case)
    assert case.decision_truth.expected_decision == "escalate"
    # The dossier embedded in the case is the same shape the agent receives, and it
    # still has no truth fields on it.
    assert "decision_truth" not in case.dossier.model_dump()

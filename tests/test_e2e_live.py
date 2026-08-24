"""The single live end-to-end run against the real Anthropic backend.

Skipped gracefully when ANTHROPIC_API_KEY is absent, so the suite is green offline. The
guardrail is deterministic, so even with a real LLM the seed set should score perfectly
and the false_approval_rate should be 0.
"""

import os

import pytest

from harness.eval.run import run_eval


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; live e2e run skipped",
)
def test_live_end_to_end_seed_set():
    from harness.llm.factory import get_llm_client

    report = run_eval(get_llm_client())
    assert report["n_cases"] == 8
    assert report["false_approval_rate"] == 0.0
    assert report["decision_accuracy_overall"] == 1.0

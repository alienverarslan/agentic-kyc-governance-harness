"""POST /screen must go through the SAME run_agent/graph as the CLI eval.

Forces the offline stub (no API key) and an in-memory store before importing the app.
"""

import os

os.environ["HARNESS_DB"] = ":memory:"
os.environ.pop("ANTHROPIC_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from harness.api.app import app  # noqa: E402
from harness.data.loader import load_dossier  # noqa: E402

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_screen_escalate_case():
    dossier = load_dossier("case_05").model_dump(mode="json")
    resp = client.post("/screen", json=dossier)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "escalate"
    assert body["trajectory"][0] == "extract"
    assert body["trajectory"][-1] == "act"
    assert body["guardrail"]["final_decision"] == "escalate"
    assert "escalate" in body["guardrail"]["required_actions"]


def test_screen_approve_case():
    dossier = load_dossier("case_01").model_dump(mode="json")
    resp = client.post("/screen", json=dossier)
    assert resp.status_code == 200
    assert resp.json()["decision"] == "approve"


def test_screen_skips_checks_when_document_missing():
    dossier = load_dossier("case_08").model_dump(mode="json")
    body = client.post("/screen", json=dossier).json()
    assert body["decision"] == "request_more_info"
    assert set(body["skipped_checks"]) == {
        "check_ownership_consistency",
        "check_authority_chain",
        "check_ubo_derivation",
    }

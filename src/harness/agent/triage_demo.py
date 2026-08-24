"""Live observation of the AI triage layer (Faz 2): run the REAL model over dossiers.

Two kinds of inputs:
* PLANTED anomalies — dossiers that pass EVERY deterministic check (so the deterministic
  decision is 'approve'), but contain an out-of-coverage red flag only an LLM would catch
  (e.g. a heavy-industry company registered at a residential apartment). We want the AI to
  raise a concern and the guardrail to move the decision off 'approve'.
* CLEAN fixtures — genuinely clean dossiers (seed #1–#3). We want the AI to stay quiet
  here; concerns raised on these estimate the false-positive (over-caution) rate.

This is an OBSERVATION script, not a deterministic test: it needs ANTHROPIC_API_KEY and a
live model, and results will vary. Run it and read the output.

Usage (from WSL, venv active):
    set -a; source .env; set +a
    python -m harness.agent.triage_demo
"""

from __future__ import annotations

from datetime import date, timedelta
from random import Random

from harness.agent.runner import run_agent
from harness.data.loader import load_dossier
from harness.generate.synthetic import clean_base


def _planted_anomalies():
    """Deterministically-clean dossiers, each with one out-of-coverage red flag."""
    # 1) Heavy-industry foundry operating from a residential apartment address.
    a = clean_base(Random(101), "NOVEL-residential-heavy-industry")
    name = "Demir Döküm Ağır Sanayi A.Ş."
    a.legal_reg = a.legal_circ = a.legal_ubo = name
    a.address = "Bahçelievler Mah. Papatya Sok. No:3 Daire 5, Çankaya, Ankara"

    # 2) Company incorporated a few weeks ago already declaring very large capital.
    b = clean_base(Random(102), "NOVEL-recent-incorporation-huge-capital")
    b.incorporation_date = b.reg_date - timedelta(days=35)
    b.share_capital = 75_000_000.0

    # 3) UBO ownership path is a narrative red flag (nominee/proxy), though the ownership
    #    is structurally clean (so every deterministic check passes).
    c = clean_base(Random(103), "NOVEL-nominee-ownership-path")
    c_dossier = c.to_dossier()
    if c_dossier.ubo is not None:
        for entry in c_dossier.ubo.declared_ubo:
            entry.ownership_path = "vekâleten üçüncü kişi (nominee) üzerinden dolaylı pay sahipliği"

    return [
        ("residential address vs heavy industry", a.to_dossier()),
        ("weeks-old company, 75M capital", b.to_dossier()),
        ("nominee ownership-path narrative", c_dossier),
    ]


def _print_run(label: str, dossier, client) -> bool:
    result = run_agent(dossier, client, case_ref=dossier.dossier_id)
    concerns = result.triage.novel_concerns if result.triage else []
    print(f"\n[{label}]  {dossier.dossier_id}")
    print(f"    decision : {result.decision}")
    if concerns:
        for c in concerns:
            print(f"    concern  : ({c.severity}) {c.detail}")
    else:
        print("    concern  : (none)")
    approved = result.decision == "approve"
    if approved and concerns:
        print("    !! WARNING: approved despite a concern (should be impossible)")
    return approved


def main() -> None:
    try:
        from harness.llm.factory import get_llm_client

        client = get_llm_client()
    except Exception as exc:  # noqa: BLE001
        print(f"Live model unavailable ({exc}). Set ANTHROPIC_API_KEY and retry.")
        return

    print("=" * 70)
    print("PLANTED ANOMALIES (deterministically clean; expect AI to raise concern):")
    for label, dossier in _planted_anomalies():
        _print_run("anomaly", dossier, client)

    print("\n" + "=" * 70)
    print("CLEAN FIXTURES (expect AI to stay quiet; concerns here = false positives):")
    fp = 0
    for case_id in ("case_01", "case_02", "case_03"):
        result = run_agent(load_dossier(case_id), client, case_ref=case_id)
        n = len(result.triage.novel_concerns) if result.triage else 0
        fp += 1 if n else 0
        print(f"\n[clean]   {case_id}")
        print(f"    decision : {result.decision}")
        print(f"    concerns : {n}")
        for c in (result.triage.novel_concerns if result.triage else []):
            print(f"      - ({c.severity}) {c.detail}")
    print("\n" + "=" * 70)
    print(f"false-positive fixtures (concern raised on a clean case): {fp}/3")
    print("Invariant to verify above: NO planted anomaly was 'approve'.")


if __name__ == "__main__":
    main()

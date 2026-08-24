"""Loaders for the seed dossiers, with a HARD separation between the agent path and
the ground-truth path.

* ``load_dossier`` — the AGENT path. Returns a ``Dossier`` and never reads, returns, or
  attaches ``decision_truth``. This is the only loader the agent/API/graph use.
* ``load_case``    — the EVAL path. Returns a ``Case`` (dossier + truth). Only the
  metrics harness uses it.

The two paths read the same file but construct disjoint model types, so the agent code
structurally cannot obtain the ground truth (asserted in test_contract_separation).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.contracts.documents import Dossier
from harness.contracts.truth import Case

SEED_DIR = Path(__file__).parent / "seed"


def _resolve(case: str | Path) -> Path:
    """Accept a case id ("case_01" / "01" / "1"), a filename, or a full path."""
    p = Path(case)
    if p.exists():
        return p
    stem = str(case)
    if stem.isdigit():
        stem = f"case_{int(stem):02d}"
    if not stem.startswith("case_"):
        stem = f"case_{stem}"
    candidate = SEED_DIR / f"{stem}.json"
    if not candidate.exists():
        raise FileNotFoundError(f"seed case not found: {case} (looked for {candidate})")
    return candidate


def _read_raw(case: str | Path) -> dict[str, Any]:
    return json.loads(_resolve(case).read_text(encoding="utf-8"))


def _dossier_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten the seed layout into a Dossier payload (dossier_id + the 3 documents)."""
    return {"dossier_id": raw["dossier_id"], **raw["dossier"]}


def load_dossier(case: str | Path) -> Dossier:
    """AGENT PATH: return only the dossier. Ground truth is never touched here."""
    raw = _read_raw(case)
    return Dossier.model_validate(_dossier_payload(raw))


def load_case(case: str | Path) -> Case:
    """EVAL PATH: return dossier + ground truth. Eval harness only."""
    raw = _read_raw(case)
    return Case.model_validate(
        {"dossier": _dossier_payload(raw), "decision_truth": raw["decision_truth"]}
    )


def list_seed_ids() -> list[str]:
    """Sorted list of seed case ids (e.g. ['case_01', ..., 'case_08'])."""
    return sorted(p.stem for p in SEED_DIR.glob("case_*.json"))

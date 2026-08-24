"""P2 — loaders for the FROZEN, hand-authored holdout corpus.

This module is the ONLY route into ``src/harness/data/holdout/``. Everything about it is
shaped by one requirement: the holdout must never influence the thing it measures.

Why a separate module (and not more functions in ``loader.py``)
--------------------------------------------------------------
Physical separation makes the isolation claim *testable by import graph* rather than by
convention. ``tests/test_holdout_isolation.py`` asserts that nothing under
``harness/agent/``, ``harness/generate/``, or ``harness/rules/`` imports this module — a
grep-able, mechanically-checked property. If these loaders lived in ``loader.py``
(imported by the gate, the API, and the eval runner alike) that assertion would be
impossible to state.

The module is named ``holdout_corpus`` rather than ``holdout`` on purpose: the fixtures
live in a sibling directory named ``holdout/``, and a module and a directory sharing a
name inside the same package is an import-resolution ambiguity waiting to happen (today
the module wins over the namespace-package directory; the day someone adds an
``__init__.py`` there, it silently stops winning).

Scope of the corpus (load-bearing, see docs/p2_design.md)
---------------------------------------------------------
This is an **in-coverage** holdout: 18 hand-authored cases whose issues all fall inside
the deterministic checks' EXISTING coverage. It measures generalization to novel *cases*,
not to novel *phenomena*. Out-of-coverage generalization is P4's job, where a nonzero
deterministic false-approval rate is the expected finding rather than a violated
invariant.

Consequently: a false_approval_rate of 0 observed on this corpus is an **empirical,
corpus-scoped rate** — it depends on the checks actually catching each authored case, and
it could in principle be nonzero. It is NOT a structural guarantee. The only structural
guarantee in this project is "a SYSTEM-origin finding implies no approve", which is
verified by construction and by test, never by a rate on a corpus.

Agent/truth separation
----------------------
Mirrors ``loader.py`` exactly: ``load_holdout_dossier`` returns a ``Dossier`` and can
never surface ground truth; ``load_holdout_case`` returns a ``Case`` and is the sole route
to ``DecisionTruth``. The two construct disjoint model types, so the separation is
structural rather than documented.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.contracts.documents import Dossier
from harness.contracts.truth import Case

HOLDOUT_DIR = Path(__file__).parent / "holdout"
MANIFEST_PATH = HOLDOUT_DIR / "manifest.json"

# Filenames are ``case_hold_NN.json``; the public case id is ``hold_NN``.
_FILE_PREFIX = "case_"
_GLOB = "case_hold_*.json"


def list_holdout_ids() -> list[str]:
    """Sorted holdout case ids, e.g. ``['hold_01', ..., 'hold_18']``."""
    return sorted(p.stem[len(_FILE_PREFIX):] for p in HOLDOUT_DIR.glob(_GLOB))


def _resolve(case_id: str) -> Path:
    """Resolve a holdout case id to its file. Deliberately strict — unlike the seed
    loader's convenience forms, only a canonical ``hold_NN`` id is accepted, so a typo
    can never silently read some other corpus's file."""
    if not case_id.startswith("hold_"):
        raise ValueError(
            f"not a holdout case id: {case_id!r} (expected the form 'hold_01'). "
            "The holdout loader never falls back to another corpus."
        )
    path = HOLDOUT_DIR / f"{_FILE_PREFIX}{case_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"holdout case not found: {case_id} (looked for {path})")
    return path


def _read_raw(case_id: str) -> dict[str, Any]:
    return json.loads(_resolve(case_id).read_text(encoding="utf-8"))


def _dossier_payload(raw: dict[str, Any]) -> dict[str, Any]:
    return {"dossier_id": raw["dossier_id"], **raw["dossier"]}


def load_holdout_dossier(case_id: str) -> Dossier:
    """AGENT-INPUT PATH: return only the dossier. Ground truth is never touched here."""
    return Dossier.model_validate(_dossier_payload(_read_raw(case_id)))


def load_holdout_case(case_id: str) -> Case:
    """EVAL PATH: dossier + ground truth. The holdout report is the sole consumer."""
    raw = _read_raw(case_id)
    return Case.model_validate(
        {"dossier": _dossier_payload(raw), "decision_truth": raw["decision_truth"]}
    )


def case_payload(case_id: str) -> dict[str, Any]:
    """The ``{dossier, decision_truth}`` content of one case, normalized through the
    contract models before hashing.

    Hashing the PARSED-AND-REDUMPED content rather than the raw file bytes is deliberate:
    it makes the pin invariant to whitespace, key order, and line endings (this repo has
    ``core.autocrlf=true`` on the Windows side, so byte-level pinning would be fragile),
    while still detecting every change that could alter a case's meaning.
    """
    case = load_holdout_case(case_id)
    return {
        "dossier": case.dossier.model_dump(mode="json"),
        "decision_truth": case.decision_truth.model_dump(mode="json"),
    }


def holdout_payloads() -> list[tuple[str, dict[str, Any]]]:
    """``(case_id, payload)`` pairs for every holdout case, for corpus-level hashing."""
    return [(cid, case_payload(cid)) for cid in list_holdout_ids()]


def load_manifest() -> dict[str, Any]:
    """The frozen corpus manifest (hashes, version, freeze-time snapshot, corrections)."""
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"holdout manifest missing at {MANIFEST_PATH}; run "
            "`python -m harness.data.freeze_holdout` to generate it"
        )
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

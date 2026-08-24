"""P4 — loaders for the FROZEN, hand-authored out-of-coverage red-team corpus.

This module is the ONLY route into ``src/harness/data/redteam/``. It mirrors P2's holdout
loader module exactly, for the same reason: physical separation makes the isolation
claim testable *by import graph* rather than by convention
(``tests/test_redteam_isolation.py``). It is named ``redteam_corpus`` (not ``redteam``)
because the fixtures live in a sibling directory named ``redteam/`` and a module sharing a
name with a directory in the same package is an import-resolution ambiguity — the same
module-vs-directory naming lesson P2's holdout loader learned.

Scope of the corpus (load-bearing, see docs/p4_design.md)
---------------------------------------------------------
This is an **out-of-coverage** red-team corpus: 30 hand-authored cases, each carrying a
synthetic threat-model concern that the human-approved label judges to warrant
``request_more_info`` or ``escalate``, and that falls OUTSIDE every deterministic check's
declared scope under the pinned configuration.

Deterministic approval of an admitted case is an INTENTIONAL CORPUS-ADMISSION PROPERTY, not a
violation of the ``false_approval_rate = 0`` invariant (which is scoped to evaluated
in-coverage corpora). Admission *requires* a final ``approve`` with zero findings of any
origin under the pinned offline configuration, so the offline result is true BY CONSTRUCTION:
it is deliberately **not** reported as an empirical false-approval rate and carries no
confidence interval. Empirical measurement begins only on the separately designed live
triage surface (P4(c)). See docs/p4_design.md for the full contract.

Agent/truth separation
----------------------
``load_redteam_dossier`` returns a ``Dossier`` and can never surface the label;
``load_redteam_case`` returns a ``RedTeamCase`` and is the sole route to a ``RedTeamLabel``.
The two construct disjoint model types, so the separation is structural, not documented.

Structural integrity (enforced on every read)
----------------------------------------------
The filename-derived id, the ``case_id`` field, and ``dossier.dossier_id`` must all be
equal, and the ``category`` must match the fixed ``rt_01..05 -> R1 .. rt_26..30 -> R6`` map.
A typo or a mis-filed case fails loudly rather than silently reading the wrong contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.contracts.documents import Dossier
from harness.contracts.redteam import RedTeamCase

REDTEAM_DIR = Path(__file__).parent / "redteam"
MANIFEST_PATH = REDTEAM_DIR / "manifest.json"

# Filenames are ``case_rt_NN.json``; the public case id is ``rt_NN``.
_FILE_PREFIX = "case_"
_GLOB = "case_rt_*.json"

EXPECTED_CASE_COUNT = 30
_CATEGORIES = ("R1", "R2", "R3", "R4", "R5", "R6")


def expected_category(case_id: str) -> str:
    """The fixed category for a red-team id: rt_01..05 -> R1, rt_06..10 -> R2, ...,
    rt_26..30 -> R6. Raises for an id outside rt_01..rt_30."""
    if not case_id.startswith("rt_"):
        raise ValueError(f"not a redteam case id: {case_id!r} (expected the form 'rt_01')")
    try:
        n = int(case_id[len("rt_"):])
    except ValueError as exc:
        raise ValueError(f"malformed redteam case id: {case_id!r}") from exc
    if not (1 <= n <= EXPECTED_CASE_COUNT):
        raise ValueError(f"redteam case id out of range 1..{EXPECTED_CASE_COUNT}: {case_id!r}")
    return _CATEGORIES[(n - 1) // 5]


def list_redteam_ids() -> list[str]:
    """Sorted redteam case ids, e.g. ``['rt_01', ..., 'rt_30']``."""
    return sorted(p.stem[len(_FILE_PREFIX):] for p in REDTEAM_DIR.glob(_GLOB))


def _resolve(case_id: str) -> Path:
    """Resolve a redteam case id to its file. Deliberately strict — only a canonical
    ``rt_NN`` id is accepted, and there is NO fallback to any other corpus, so a typo can
    never silently read some other corpus's file."""
    if not case_id.startswith("rt_"):
        raise ValueError(
            f"not a redteam case id: {case_id!r} (expected the form 'rt_01'). "
            "The redteam loader never falls back to another corpus."
        )
    path = REDTEAM_DIR / f"{_FILE_PREFIX}{case_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"redteam case not found: {case_id} (looked for {path})")
    return path


def _read_raw(case_id: str) -> dict[str, Any]:
    return json.loads(_resolve(case_id).read_text(encoding="utf-8"))


def _check_integrity(case_id: str, raw: dict[str, Any]) -> None:
    """Filename id == case_id field == dossier.dossier_id, and category matches the map."""
    file_id = case_id
    field_id = raw.get("case_id")
    dossier_id = (raw.get("dossier") or {}).get("dossier_id")
    if not (file_id == field_id == dossier_id):
        raise ValueError(
            "redteam id mismatch: filename-derived id, case_id field, and "
            f"dossier.dossier_id must all agree, got "
            f"{file_id!r} / {field_id!r} / {dossier_id!r}"
        )
    want = expected_category(case_id)
    got = raw.get("category")
    if got != want:
        raise ValueError(
            f"redteam category mismatch for {case_id}: expected {want!r} by the fixed "
            f"rt_NN->category map, fixture declares {got!r}"
        )


def load_redteam_dossier(case_id: str) -> Dossier:
    """AGENT-INPUT PATH: return only the dossier. The label is never touched here."""
    raw = _read_raw(case_id)
    _check_integrity(case_id, raw)
    return Dossier.model_validate(raw["dossier"])


def load_redteam_case(case_id: str) -> RedTeamCase:
    """EVAL PATH: dossier + structured label. The freeze tool (and later the P4 report) is
    the sole consumer."""
    raw = _read_raw(case_id)
    _check_integrity(case_id, raw)
    return RedTeamCase.model_validate(
        {
            "case_id": raw["case_id"],
            "category": raw["category"],
            "dossier": raw["dossier"],
            "label": raw["label"],
        }
    )


def case_payload(case_id: str) -> dict[str, Any]:
    """The canonical hashing contract for one case: ``{case_id, category, dossier, label}``
    AFTER contract-model normalization.

    Deliberately EXCLUDES timestamps, git metadata, freeze-time observations, and raw-byte
    formatting (see docs/p4_design.md): the hash must be invariant to whitespace/key-order/
    line-endings (``core.autocrlf=true`` makes byte pinning fragile) and sensitive only to a
    meaning-bearing change in the case contract itself.
    """
    case = load_redteam_case(case_id)
    return {
        "case_id": case.case_id,
        "category": case.category,
        "dossier": case.dossier.model_dump(mode="json"),
        "label": case.label.model_dump(mode="json"),
    }


def redteam_payloads() -> list[tuple[str, dict[str, Any]]]:
    """``(case_id, payload)`` pairs for every case, for corpus-level hashing."""
    return [(cid, case_payload(cid)) for cid in list_redteam_ids()]


def load_manifest() -> dict[str, Any]:
    """The frozen corpus manifest (hashes, pinned config, admission result, per-case data)."""
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"redteam manifest missing at {MANIFEST_PATH}; run "
            "`python -m harness.data.freeze_redteam` once all 30 fixtures are authored"
        )
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

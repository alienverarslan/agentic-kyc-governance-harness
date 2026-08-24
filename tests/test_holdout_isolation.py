"""P2 — isolation: the holdout must never influence the thing it measures.

A holdout corpus is worthless if any part of the system it evaluates has been tuned,
gated, or generated against it. These tests enforce that structurally, along two axes:

* **Static (import graph)** — no module the harness runs at decision time may even be able
  to read the holdout. This is the strong form: not "doesn't", but "can't".
* **Behavioral** — the validation gate, the synthetic generator, and the anomaly corpus
  demonstrably do not contain holdout cases.

Plus the same hard agent/truth separation the seed corpus has: the agent-input path
returns a ``Dossier`` and structurally cannot surface ``DecisionTruth``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.contracts.documents import Dossier
from harness.contracts.truth import Case
from harness.data.holdout_corpus import (
    HOLDOUT_DIR,
    list_holdout_ids,
    load_holdout_case,
    load_holdout_dossier,
)

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "harness"

# Packages that participate in producing or gating a decision. None of them may reach the
# holdout. ``harness.eval`` is not swept wholesale here because the future holdout report
# lives there — but it is NOT blanket-authorized either; see the sole-consumer test below,
# which allowlists exact module paths only.
DECISION_PATH_PACKAGES = ("agent", "generate", "rules", "api", "llm", "normalize", "store")

# Textual markers for any route into the holdout.
HOLDOUT_MARKERS = ("holdout_corpus", "load_holdout_case", "load_holdout_dossier", "data/holdout")

# The ONLY modules permitted to read the holdout. Exact paths, never a directory prefix:
# a prefix such as "eval/" would silently authorize the development eval runner. Each
# addition is a visible, reviewed diff — "eval/holdout_report.py" was added by P2 commit (b)
# as the dedicated holdout-report path that commit (a) said would be named explicitly.
SOLE_CONSUMERS = {
    "data/holdout_corpus.py",
    "data/freeze_holdout.py",
    "eval/holdout_report.py",
}


def _python_sources(package: str) -> list[Path]:
    return sorted((SRC_ROOT / package).rglob("*.py"))


# --------------------------------------------------------------------------------------
# Static isolation — the strong form
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("package", DECISION_PATH_PACKAGES)
def test_decision_path_packages_never_reference_the_holdout(package: str):
    """The gate, the generator, the anomaly builders, the agent graph and the API must not
    contain any route into the holdout — not an import, not a path literal."""
    offenders: list[str] = []
    for path in _python_sources(package):
        text = path.read_text(encoding="utf-8")
        for marker in HOLDOUT_MARKERS:
            if marker in text:
                offenders.append(f"{path.relative_to(SRC_ROOT)} contains {marker!r}")
    assert offenders == [], (
        "a decision-path module can reach the frozen holdout, which would invalidate every "
        f"number measured on it: {offenders}"
    )


def test_only_expected_modules_consume_the_holdout():
    """Sole-consumer check across the whole package — EXACT module paths only.

    The allowlist is deliberately a set of exact paths, never a directory prefix. An earlier
    revision allowed the whole ``eval/`` prefix on the reasoning that the future holdout
    report lives there; that was too broad, because it would silently authorize
    ``eval/run.py`` — the DEVELOPMENT eval runner — to read the holdout and still pass this
    test. The approved access rule is a dedicated holdout-report path, not a directory.

    When commit (b) adds that report module, its exact path is added here in the same commit,
    so widening the holdout's reader set is always a visible, reviewed diff.
    """
    consumers = set()
    for path in SRC_ROOT.rglob("*.py"):
        rel = path.relative_to(SRC_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in HOLDOUT_MARKERS):
            consumers.add(rel)

    unexpected = consumers - SOLE_CONSUMERS
    assert unexpected == set(), f"unexpected holdout consumers: {sorted(unexpected)}"


@pytest.mark.parametrize(
    "dev_module",
    ["eval/run.py", "eval/compound.py", "eval/metrics.py", "agent/runner.py", "rules/gate.py"],
)
def test_a_development_loop_module_is_not_an_authorized_consumer(dev_module: str):
    """Guards the allowlist from regressing to a directory-wide rule.

    An earlier revision allowed the whole ``eval/`` prefix, which would have authorized
    ``eval/run.py`` — the development eval runner — to read the holdout while still passing
    the sole-consumer test. Membership is asserted behaviorally, on the allowlist itself, so
    reintroducing any prefix rule that admits these paths fails here.
    """
    assert dev_module not in SOLE_CONSUMERS


# --------------------------------------------------------------------------------------
# Behavioral isolation
# --------------------------------------------------------------------------------------
def _holdout_dossier_ids() -> set[str]:
    return {load_holdout_dossier(cid).dossier_id for cid in list_holdout_ids()}


def test_validation_gate_corpus_excludes_the_holdout():
    """The gate decides whether a learned rule may be promoted. If the holdout were in its
    corpus, every promoted rule would have been implicitly fitted to the holdout."""
    from harness.rules.gate import _known_dossiers

    gate_ids = {dossier.dossier_id for _cid, dossier in _known_dossiers(per_code=1, seed=42)}
    assert gate_ids.isdisjoint(_holdout_dossier_ids())
    assert not any(cid.startswith("hold_") for cid, _d in _known_dossiers(per_code=1, seed=42))


def test_generator_never_produces_holdout_cases():
    from harness.generate.synthetic import generate_cases

    generated = generate_cases(per_code=1, seed=42)
    generated_ids = {gc.case.dossier.dossier_id for gc in generated}
    assert generated_ids.isdisjoint(_holdout_dossier_ids())
    assert not any(gc.case_id.startswith("hold_") for gc in generated)


def test_anomaly_corpus_excludes_the_holdout():
    from harness.agent.anomaly_corpus import build_anomaly_corpus

    corpus = build_anomaly_corpus(seed=42, per_type=1)
    assert {c.dossier.dossier_id for c in corpus}.isdisjoint(_holdout_dossier_ids())


def test_holdout_files_live_outside_every_generated_corpus_path():
    """The holdout is version-controlled data, never written by a generator at run time."""
    assert HOLDOUT_DIR.is_dir()
    assert sorted(p.name for p in HOLDOUT_DIR.glob("case_hold_*.json")) == [
        f"case_hold_{i:02d}.json" for i in range(1, 19)
    ]


# --------------------------------------------------------------------------------------
# Agent/truth separation (same contract as the seed corpus)
# --------------------------------------------------------------------------------------
def test_agent_input_path_cannot_surface_ground_truth():
    dossier = load_holdout_dossier("hold_01")
    assert isinstance(dossier, Dossier)
    assert not isinstance(dossier, Case)
    assert not hasattr(dossier, "decision_truth")
    assert "decision_truth" not in Dossier.model_fields
    assert "expected_decision" not in Dossier.model_fields


def test_eval_path_is_the_only_route_to_truth():
    case = load_holdout_case("hold_01")
    assert isinstance(case, Case)
    assert case.decision_truth.expected_decision == "approve"


def test_holdout_loader_refuses_non_holdout_ids():
    """No silent fallback to the seed corpus on a typo'd id."""
    with pytest.raises(ValueError):
        load_holdout_dossier("case_01")
    with pytest.raises(FileNotFoundError):
        load_holdout_dossier("hold_99")

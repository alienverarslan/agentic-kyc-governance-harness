"""P4 — isolation: the red-team corpus must never influence the thing it measures.

Same strong form as the P2 holdout isolation test, and for the same reason: a corpus used
to measure the system is worthless if any decision-path module can read it. Here the checks
are STATIC (import graph) and MODEL-LEVEL only — the behavioral disjointness checks
(generator/gate/anomaly) and the file-count check are deferred to the fixtures deliverable,
because they require the 30 authored fixtures that do not exist yet. The sole-consumer
allowlist is exact module paths, never a directory prefix (the ``eval/run.py`` lesson).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.contracts.documents import Dossier
from harness.contracts.redteam import RedTeamCase

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "harness"

# Packages that participate in producing or gating a decision. None may reach the red-team
# corpus. ``harness.eval`` is not swept wholesale (the future P4 report will live there) but
# is NOT blanket-authorized either — see the sole-consumer test.
DECISION_PATH_PACKAGES = ("agent", "generate", "rules", "api", "llm", "normalize", "store")

# Textual markers for any route into the red-team corpus.
REDTEAM_MARKERS = ("redteam_corpus", "load_redteam_case", "load_redteam_dossier", "data/redteam")

# The ONLY modules permitted to read the red-team corpus. Exact paths, never a directory
# prefix. The eventual P4 offline report path is added here in the SAME commit that
# introduces it, so widening the reader set is always a visible, reviewed diff.
SOLE_CONSUMERS = {
    "data/redteam_corpus.py",
    "data/freeze_redteam.py",
    "eval/redteam_record.py",
    "eval/redteam_live.py",
}


def _python_sources(package: str) -> list[Path]:
    return sorted((SRC_ROOT / package).rglob("*.py"))


@pytest.mark.parametrize("package", DECISION_PATH_PACKAGES)
def test_decision_path_packages_never_reference_the_redteam_corpus(package: str):
    offenders: list[str] = []
    for path in _python_sources(package):
        text = path.read_text(encoding="utf-8")
        for marker in REDTEAM_MARKERS:
            if marker in text:
                offenders.append(f"{path.relative_to(SRC_ROOT)} contains {marker!r}")
    assert offenders == [], (
        "a decision-path module can reach the frozen red-team corpus, which would invalidate "
        f"every number measured on it: {offenders}"
    )


def test_only_expected_modules_consume_the_redteam_corpus():
    consumers = set()
    for path in SRC_ROOT.rglob("*.py"):
        rel = path.relative_to(SRC_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in REDTEAM_MARKERS):
            consumers.add(rel)
    unexpected = consumers - SOLE_CONSUMERS
    assert unexpected == set(), f"unexpected red-team consumers: {sorted(unexpected)}"


@pytest.mark.parametrize(
    "dev_module",
    ["eval/run.py", "eval/compound.py", "eval/metrics.py", "agent/runner.py", "rules/gate.py"],
)
def test_a_development_loop_module_is_not_an_authorized_consumer(dev_module: str):
    """Guards the allowlist from regressing to a directory-wide rule (the ``eval/`` prefix
    that would silently authorize the development eval runner)."""
    assert dev_module not in SOLE_CONSUMERS


# --------------------------------------------------------------------------------------
# Agent/truth separation, asserted at the model level (no fixtures required yet)
# --------------------------------------------------------------------------------------
def test_dossier_cannot_structurally_surface_the_label():
    assert "label" not in Dossier.model_fields
    assert "decision_truth" not in Dossier.model_fields
    assert not issubclass(Dossier, RedTeamCase)

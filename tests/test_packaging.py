"""Packaging regression gate for the frozen corpora.

The distributions once shipped no corpus at all. `pyproject.toml` declared package data
under `harness.data.seed` and `harness.data.holdout`, and neither is a package — they are
plain directories with no `__init__.py` — so setuptools matched nothing, silently. Editable
installs and checkout-based test runs never noticed, because both read the source tree.

These tests are the fast gate: they check the DECLARATION, so a corpus file added to a
directory that no glob covers fails here in milliseconds. The slow gate is
`scripts/verify_distribution.sh`, which builds a wheel and an sdist, asserts the files are
inside both, and loads them from an installed wheel outside the checkout. CI runs both.
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
DATA_ROOT = REPO_ROOT / "src" / "harness" / "data"

#: Every corpus a loader resolves at import or call time, with its expected size.
EXPECTED_COUNTS = {
    "redteam": 31,   # 30 cases + manifest.json
    "holdout": 19,   # 18 cases + manifest.json
    "seed": 10,      # 8 cases + MANIFEST.md + one further json
}


@pytest.fixture(scope="module")
def package_data() -> dict[str, list[str]]:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return config["tool"]["setuptools"]["package-data"]


def _declared_packages(package_data: dict[str, list[str]]) -> set[str]:
    return set(package_data)


def test_package_data_only_declares_real_packages(package_data):
    """A package-data key naming a non-package matches nothing and ships nothing.

    This is the exact defect that hid the missing corpora, so it is asserted directly.
    """
    for dotted in _declared_packages(package_data):
        pkg_dir = REPO_ROOT / "src" / Path(*dotted.split("."))
        assert (pkg_dir / "__init__.py").is_file(), (
            f"package-data key {dotted!r} does not name a package: "
            f"{pkg_dir}/__init__.py does not exist, so its globs match nothing"
        )


def test_every_data_file_is_covered_by_a_package_data_glob(package_data):
    """Every shipped data file must be matched by some declared glob."""
    uncovered: list[str] = []
    for path in sorted(DATA_ROOT.rglob("*")):
        if not path.is_file() or path.suffix == ".py" or "__pycache__" in path.parts:
            continue
        covered = False
        for dotted, patterns in package_data.items():
            pkg_dir = REPO_ROOT / "src" / Path(*dotted.split("."))
            try:
                rel = path.relative_to(pkg_dir).as_posix()
            except ValueError:
                continue
            if any(fnmatch.fnmatch(rel, pattern) for pattern in patterns):
                covered = True
                break
        if not covered:
            uncovered.append(path.relative_to(REPO_ROOT).as_posix())
    assert uncovered == [], (
        "these data files are not covered by any package-data glob and would be missing "
        f"from the built wheel and sdist: {uncovered}"
    )


@pytest.mark.parametrize("subdir, expected", sorted(EXPECTED_COUNTS.items()))
def test_corpus_directories_have_their_expected_size(subdir, expected):
    """Guards against a corpus shrinking without anyone noticing."""
    files = [p for p in (DATA_ROOT / subdir).iterdir() if p.is_file()]
    assert len(files) == expected, f"{subdir}/ holds {len(files)} files, expected {expected}"


def test_the_thirty_frozen_redteam_cases_are_present_and_named_exactly():
    ids = sorted(p.stem for p in (DATA_ROOT / "redteam").glob("case_rt_*.json"))
    assert ids == [f"case_rt_{i:02d}" for i in range(1, 31)]
    assert (DATA_ROOT / "redteam" / "manifest.json").is_file()

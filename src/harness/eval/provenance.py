"""Run provenance capture for the P5 scientific report.

Every P5 artifact must be traceable back to the exact code, corpus, and environment that
produced it. This module is the ONLY place that touches git, package metadata, or hashes
raw generated content — kept isolated from ``scientific_report.py`` so the aggregator
itself stays a pure function and this module's I/O can be tested in isolation (subprocess
calls mocked, no dependency on the actual repo's dirty state at test time).

Failure policy (deliberately NOT one-size-fits-all — see docs/p5_design.md):
* git metadata unavailable (no git binary, not a repo) -> explicit ``None``, never raised.
* dirty-state unable to be determined -> explicit ``None``, NEVER ``False``. A false "clean"
  claim is the one place this module must not guess, because it is a reproducibility claim
  a reader could rely on.
* corpus/rule hashing itself never silently degrades: if the caller cannot supply the
  content to hash, that is a bug in the caller, not something this module papers over.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import tomllib
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def _run_git(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def git_info() -> dict[str, Any]:
    """``{"commit_sha": str | None, "dirty": bool | None}``.

    ``dirty`` is computed from ``git status --porcelain`` (NOT ``git diff --quiet``,
    which misses untracked files entirely) — any output, tracked or untracked, means
    dirty. Undeterminable is reported as ``None``, never coerced to ``False``.
    """
    sha_out = _run_git(["rev-parse", "HEAD"])
    commit_sha = sha_out.strip() if sha_out is not None else None

    status_out = _run_git(["status", "--porcelain", "--untracked-files=normal"])
    dirty = (len(status_out.strip()) > 0) if status_out is not None else None

    return {"commit_sha": commit_sha, "dirty": dirty}


def git_branch() -> str | None:
    """Current branch name via ``git rev-parse --abbrev-ref HEAD``. ``None`` when
    undeterminable (no git binary, not a repo) or on a detached HEAD (``"HEAD"``), so a
    caller never records a misleading branch. Kept here so all git access stays in this
    single module (see the module docstring)."""
    out = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if out is None:
        return None
    branch = out.strip()
    return None if branch in ("", "HEAD") else branch


def python_version() -> str:
    return platform.python_version()


def _load_pyproject() -> dict[str, Any]:
    with PYPROJECT_PATH.open("rb") as f:
        return tomllib.load(f)


def _dependency_names() -> list[str]:
    """Bare package names from ``[project] dependencies`` (strip version specifiers)."""
    deps: list[str] = _load_pyproject()["project"]["dependencies"]
    names = []
    for spec in deps:
        name = spec
        for sep in ("[", ">=", "<=", "==", "!=", "~=", ">", "<"):
            name = name.split(sep, 1)[0]
        names.append(name.strip())
    return names


def harness_version() -> str:
    return str(_load_pyproject()["project"]["version"])


def package_versions() -> dict[str, str | None]:
    """Installed version of every declared runtime dependency, derived from
    ``pyproject.toml`` (never hardcoded) so this never drifts out of sync with actual
    dependencies. ``None`` for a package that cannot be resolved (not installed)."""
    versions: dict[str, str | None] = {}
    for name in _dependency_names():
        try:
            versions[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def canonical_json_bytes(obj: Any) -> bytes:
    """Canonical serialization for hashing: sorted keys, compact separators, UTF-8.
    Non-ASCII (Turkish text) is preserved, never escaped, so the hash is stable
    regardless of ``ensure_ascii`` choices made elsewhere in the codebase."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sha256_of(obj: Any) -> str:
    digest = hashlib.sha256(canonical_json_bytes(obj)).hexdigest()
    return f"sha256:{digest}"


def hash_corpus(payloads: list[tuple[str, dict[str, Any]]]) -> str:
    """Hash of the corpus's actual generated CONTENT, not its seed.

    ``payloads`` is a list of (case_id, JSON-serializable payload) pairs — e.g. each
    case's dossier + decision_truth, already ``model_dump(mode="json")``-ed by the caller.
    Sorted by case_id here so the hash is independent of iteration order, and independent
    of the seed integer alone: a generator code change that alters output for the SAME
    seed changes this hash, which is the whole point.
    """
    ordered = dict(sorted(payloads, key=lambda item: item[0]))
    return sha256_of(ordered)


def hash_promoted_rules(rules: list[dict[str, Any]]) -> str:
    """Hash of the currently-promoted rule set. ``rules`` are plain dicts (e.g.
    ``CandidateRule.model_dump()``), sorted by ``rule_id`` for determinism."""
    ordered = sorted(rules, key=lambda r: r["rule_id"])
    return sha256_of(ordered)


def build_run_provenance(*, run_type: str, timestamp_utc: str, run_id: str) -> dict[str, Any]:
    """Assemble the ``run`` block of the P5 artifact. Pure aside from the git/env calls
    isolated above; never raises — every unresolvable field is explicit ``None``."""
    return {
        "run_id": run_id,
        "run_type": run_type,
        "timestamp_utc": timestamp_utc,
        "git": git_info(),
        "python_version": python_version(),
        "package_versions": package_versions(),
        "harness_version": harness_version(),
        "python_executable": sys.executable,
    }

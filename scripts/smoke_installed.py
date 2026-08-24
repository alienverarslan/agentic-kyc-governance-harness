#!/usr/bin/env python3
"""Smoke-test an INSTALLED harness distribution, from outside the checkout.

Proves that the wheel a user would install actually carries the frozen corpora, by loading
them through the project's normal loaders rather than by looking at files. Must be run with
the interpreter of a virtual environment that has the wheel installed, from a working
directory outside the repository, so nothing can resolve from the source tree.

Usage:
    cd /some/tmp/dir && /path/to/venv/bin/python smoke_installed.py
"""

from __future__ import annotations

import sys
from pathlib import Path


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    import harness
    from harness.data import loader, holdout_corpus, redteam_corpus

    origin = Path(harness.__file__).resolve()
    print(f"harness resolved from : {origin}")
    print(f"working directory     : {Path.cwd()}")

    if "site-packages" not in origin.parts:
        fail(f"harness did not resolve from an installed distribution: {origin}")
    if "src" in origin.parts:
        fail(f"harness resolved from a source tree: {origin}")
    # The risk is running inside a checkout, where the source tree could shadow the wheel.
    # A venv that happens to sit under the working directory is fine and expected.
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").is_file() or (cwd / "src" / "harness").is_dir():
        fail(f"working directory looks like a project checkout: {cwd}")

    # --- frozen red-team corpus, through the normal loader -----------------------------
    manifest = redteam_corpus.load_manifest()
    ids = redteam_corpus.list_redteam_ids()
    print(f"red-team manifest     : {manifest['corpus_hash'][:26]}...")
    print(f"red-team case ids     : {len(ids)}")
    if len(ids) != 30:
        fail(f"expected 30 red-team cases, loaded {len(ids)}")
    if sorted(manifest["per_case"]) != sorted(f"rt_{i:02d}" for i in range(1, 31)):
        fail("manifest per_case ids do not match rt_01..rt_30")

    for case_id in ids:
        dossier = redteam_corpus.load_redteam_dossier(case_id)
        if dossier is None:
            fail(f"{case_id}: loader returned nothing")
    print(f"red-team dossiers     : {len(ids)} loaded and validated")

    # --- the other corpora the same packaging bug would have dropped -------------------
    holdout_ids = holdout_corpus.list_holdout_ids()
    print(f"holdout case ids      : {len(holdout_ids)}")
    if len(holdout_ids) != 18:
        fail(f"expected 18 holdout cases, loaded {len(holdout_ids)}")

    seed_ids = loader.list_seed_ids()
    print(f"seed case ids         : {len(seed_ids)}")
    if len(seed_ids) != 8:
        fail(f"expected 8 seed cases, loaded {len(seed_ids)}")

    print("OK: installed distribution carries every frozen corpus")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Packaging regression gate: assert every data corpus is actually inside the built
wheel and sdist.

This exists because the package-data configuration once named `harness.data.seed` and
`harness.data.holdout`, neither of which is a package — they are plain directories with no
``__init__.py`` — so setuptools matched nothing and every corpus was silently missing from
the distributions. Editable installs and checkout-based test runs hid it completely, because
both read the source tree.

Run against a ``dist/`` directory holding exactly one wheel and one sdist.

Usage:
    python scripts/check_distribution.py dist
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from pathlib import Path

#: (subdirectory, filename pattern, exact expected count)
REQUIRED: tuple[tuple[str, str, int], ...] = (
    ("redteam", r"case_rt_\d{2}\.json", 30),
    ("redteam", r"manifest\.json", 1),
    ("holdout", r"case_hold_\d{2}\.json", 18),
    ("holdout", r"manifest\.json", 1),
    ("seed", r"case_\d{2}\.json", 8),
    ("seed", r"hash_pin\.json", 1),   # the seed hash pin, read alongside the cases
    ("seed", r"MANIFEST\.md", 1),     # non-.json data: the glob for it is separate
    ("", r"promoted_rules\.json", 1),
)

PKG = "harness/data"


def _wheel_members(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def _sdist_members(path: Path) -> list[str]:
    with tarfile.open(path, "r:gz") as tf:
        # strip the leading "<name>-<version>/" component
        return [m.name.split("/", 1)[1] for m in tf.getmembers() if "/" in m.name]


def _check(kind: str, members: list[str]) -> list[str]:
    failures: list[str] = []
    for subdir, pattern, expected in REQUIRED:
        prefix = f"{PKG}/{subdir}/" if subdir else f"{PKG}/"
        # the sdist keeps the src/ layout; the wheel does not
        regex = re.compile(rf"^(src/)?{re.escape(prefix)}{pattern}$")
        found = sorted(m for m in members if regex.match(m))
        label = f"{prefix}{pattern}"
        if len(found) != expected:
            failures.append(
                f"{kind}: expected {expected} file(s) matching {label}, found {len(found)}"
            )
        print(f"  {kind:5} {label:45} {len(found):>3} / {expected}")
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dist", type=Path, help="directory containing the built wheel and sdist")
    args = ap.parse_args(argv)

    wheels = sorted(args.dist.glob("*.whl"))
    sdists = sorted(args.dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        print(f"expected exactly one wheel and one sdist in {args.dist}, "
              f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)", file=sys.stderr)
        return 2

    print(f"wheel: {wheels[0].name}")
    print(f"sdist: {sdists[0].name}")
    failures = _check("wheel", _wheel_members(wheels[0]))
    failures += _check("sdist", _sdist_members(sdists[0]))

    if failures:
        print("\nPACKAGING REGRESSION:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1
    print("\nall required corpus files are present in both distributions")
    return 0


if __name__ == "__main__":
    sys.exit(main())

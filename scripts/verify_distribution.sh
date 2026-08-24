#!/usr/bin/env bash
# Build the distributions, check they carry every frozen corpus, then install the WHEEL
# into a throwaway environment and exercise it from outside the checkout.
#
# CI and a developer machine run exactly this script, so a green CI run means the same
# thing a local run means.
#
# Requires, already installed in the current interpreter's environment: the pinned build
# backend from requirements-build.txt. Nothing is resolved from the network here — the
# build runs with --no-isolation so it uses those pinned versions.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "=== repo: $REPO"
echo "=== interpreter: $("$PY" --version) at $(command -v "$PY")"

cd "$REPO"
rm -rf dist build
echo
echo "=== building wheel + sdist (no build isolation, pinned backend) ==="
"$PY" -m build --no-isolation --wheel --sdist --outdir dist
ls -l dist

echo
echo "=== packaging regression gate ==="
"$PY" scripts/check_distribution.py dist

echo
echo "=== installing the WHEEL into a throwaway environment ==="
"$PY" -m venv "$WORK/venv"
"$WORK/venv/bin/python" -m pip install --quiet --no-cache-dir dist/*.whl
"$WORK/venv/bin/python" -m pip list --format=freeze | sed 's/^/    /'

echo
echo "=== smoke test, run from OUTSIDE the checkout ==="
cp scripts/smoke_installed.py "$WORK/smoke_installed.py"
cd "$WORK"
"$WORK/venv/bin/python" "$WORK/smoke_installed.py"

echo
echo "=== installed console entry points ==="
for cli in harness-eval harness-p4c-public-summary harness-p4-redteam-record; do
  printf '    %-28s ' "$cli"
  if "$WORK/venv/bin/$cli" --help >/dev/null 2>&1; then echo "ok (--help)"; else echo "FAILED"; exit 1; fi
done

echo
echo "distribution verified: corpora present, wheel importable, CLIs runnable"

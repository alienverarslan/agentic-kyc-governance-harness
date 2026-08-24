"""Slice-2: parametric synthetic dossier generation.

Turns the 8 hand-authored diagnostic fixtures into statistical scale. For each taxonomy
code the generator has a recipe that perturbs a fully-consistent "clean base" dossier so
that EXACTLY ONE code is triggered and everything else stays clean; because it builds the
case, it also knows the correct answer (ground truth) without human labeling.

Correctness is enforced by the harness itself (see ``generate.selfcheck`` /
``tests/test_generator.py``): every generated case is screened and its agent findings
must equal the injected code exactly. The 8 read-only fixtures remain the gold standard —
the generator is validated against them, never the other way around.
"""

from harness.generate.synthetic import (
    GeneratedCase,
    ALL_CODES,
    generate_cases,
    generate_for_code,
)

__all__ = ["GeneratedCase", "ALL_CODES", "generate_cases", "generate_for_code"]

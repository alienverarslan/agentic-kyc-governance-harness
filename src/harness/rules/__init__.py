"""Faz 3: the rule-learning loop. A declarative rule schema (``schema``), the concrete
templates registered against it (``templates``), the offline validation gate a candidate
must pass (``gate``), and the human-approved promoted-rule store (``store``).

Importing this package (or any submodule) registers the built-in templates as a side
effect, so ``schema.TEMPLATE_REGISTRY`` is always populated without callers needing to
remember a separate import.
"""

from harness.rules import templates as _templates  # noqa: F401

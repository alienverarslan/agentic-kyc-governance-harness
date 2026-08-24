"""The screening agent: state, checks, guardrail, graph, and runner.

Kept intentionally free of eager submodule imports. ``harness.llm.stub`` imports
``harness.agent.guardrail`` for the shared policy function, so re-exporting the graph /
runner here (which pull in ``harness.llm``) would create an import cycle. Import the
submodules explicitly where needed, e.g. ``from harness.agent.runner import run_agent``.
"""

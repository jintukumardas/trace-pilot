"""tracepilot_agent — the LangGraph multi-agent orchestrator.

Public surface (see ``docs/INTERNAL_CONTRACTS.md``)::

    from tracepilot_agent import Orchestrator, build_graph
    from tracepilot_agent.runtime import RepoLocator   # Protocol

The orchestrator wires a small, grounded agent graph:

    START → router → retrieval_planner → retriever → code_analyst →
    action_planner →(needs_tools & iterations<2? tool_executor → code_analyst
                     : synthesizer) → judge → END

Every node is wrapped in a ``Tracer`` span, renders its instruction via
``tracepilot_prompts.render`` and calls the local Ollama model through
:func:`tracepilot_agent.models.complete`. The whole pipeline *fails soft*: if
Ollama is unreachable the completion layer returns a grounded-from-evidence
fallback so the graph still runs end-to-end and the user gets a useful,
citation-bearing answer instead of an exception.
"""

from __future__ import annotations

from .graph import build_graph
from .runtime import Orchestrator

__version__ = "0.1.0"

__all__ = ["Orchestrator", "build_graph", "__version__"]

"""Graph public API regression tests for migration work."""

from unittest.mock import MagicMock

from app.layer1_domain.entities.orchestrator.orchestration_state import OrchestrationState
from app.layer4_frameworks.graph.graph_builder import build_graph


def test_compile_orchestration_graph_returns_compiled_graph():
    graph = build_graph(checkpointer=None, builder_factory=MagicMock())

    assert graph is not None
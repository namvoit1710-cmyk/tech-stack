from __future__ import annotations

from typing import Any

from agent_sdk import AgentGraphBuilder

from app.layer1_domain.entities.file_state import FileState
from app.layer4_frameworks.graph.main_flow import register_main_flow


def create_csv_agent_graph_builder(
    deps: dict[str, Any] | None = None,
) -> AgentGraphBuilder:
    """Create the SDK-backed CSV graph builder in layer 4.

    The SDK ``AgentGraphBuilder`` is a framework concern. Keeping construction
    here prevents bootstrap or layer 2 application code from owning graph
    framework wiring.
    """

    return AgentGraphBuilder(deps=deps or {}, state_schema=FileState)


def build_graph(*, deps: dict[str, Any] | None = None, checkpointer: Any = None) -> Any:
    """Build and compile the CSV agent graph.

    Layer 4 owns the concrete SDK graph builder. Layer 2 nodes/use cases remain
    pure workflow/business behavior and are registered through ``main_flow``.
    """

    resolved_deps = deps or {}
    builder = create_csv_agent_graph_builder(resolved_deps)
    register_main_flow(builder, deps=resolved_deps)
    return builder.compile(checkpointer=checkpointer)

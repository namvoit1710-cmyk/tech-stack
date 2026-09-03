"""flow_graph_example.py — FlowGraphBuilder example.

Demonstrates how to use ``FlowGraphBuilder`` to compile a LangGraph
``StateGraph`` from a declarative ``FlowConfig`` description.

``FlowGraphBuilder`` separates *what* the flow does (the ``FlowConfig``)
from *how* each step is implemented (node functions registered in a
``NodeTypeRegistry``).  This is the right builder to use when the
workflow topology is driven by data (e.g. stored in a database or loaded
from JSON/YAML) rather than hard-coded graph edges.

Key SDK symbols used (no direct langgraph imports needed):
    ``FlowGraphBuilder``    — compiles a ``FlowConfig`` into a runnable graph
    ``FlowConfig``          — holds agent_type, flow_type, and ordered steps
    ``StepConfig``          — one step in the flow (id, type, routing)
    ``NodeTypeRegistry``    — maps step type strings to Python callables

This example is **self-contained** and exits 0 without any external
services or environment variables.

Usage::

    python examples/flow_graph_example.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_sdk import FlowConfig, FlowGraphBuilder, NodeTypeRegistry, StepConfig

# ---------------------------------------------------------------------------
# Step node implementations
#
# Each node function receives three arguments:
#   state       — the mutable graph state dict
#   step_config — the StepConfig for this step (id, type, params, …)
#   deps        — the shared dependency dict passed to FlowGraphBuilder
# ---------------------------------------------------------------------------


def greet_node(state: dict, step_config: StepConfig, deps: dict) -> dict:
    """Prepend a greeting to the incoming message."""
    name = state.get("name", "world")
    greeting = f"Hello, {name}!"
    print(f"[{step_config.id}] {greeting}")
    return {"greeting": greeting}


def validate_node(state: dict, step_config: StepConfig, deps: dict) -> dict:
    """Validate that a greeting was produced by the previous step.

    Note: ``StateGraph(dict)`` replaces the state with each node's return
    value.  Carry forward the ``greeting`` field so later nodes can access it.
    """
    greeting = state.get("greeting", "")
    if not greeting:
        raise ValueError("No greeting found in state — did greet_node run first?")
    print(f"[{step_config.id}] Validation passed: '{greeting}'")
    return {"greeting": greeting, "validated": True}


def summarise_node(state: dict, step_config: StepConfig, deps: dict) -> dict:
    """Summarise the result and mark the flow as done.

    Note: ``StateGraph(dict)`` replaces the state with each node's return
    value.  To carry forward earlier fields, include them in the return dict.
    """
    greeting = state.get("greeting", "")
    validated = state.get("validated", False)
    summary = f"Flow complete. Produced: {greeting!r}"
    print(f"[{step_config.id}] {summary}")
    return {
        "greeting": greeting,
        "validated": validated,
        "summary": summary,
        "status": "completed",
    }


# ---------------------------------------------------------------------------
# Registry
#
# Register each node type by a string key.  The keys must match the ``type``
# field used in StepConfig instances below.
# ---------------------------------------------------------------------------


def build_registry() -> NodeTypeRegistry:
    registry = NodeTypeRegistry()
    registry.register("greet", greet_node)
    registry.register("validate", validate_node)
    registry.register("summarise", summarise_node)
    return registry


# ---------------------------------------------------------------------------
# Flow configuration
#
# ``FlowConfig`` is a plain dataclass — it can be constructed in code as
# shown here, or deserialized from JSON/YAML in production systems.
# ---------------------------------------------------------------------------


def build_flow_config() -> FlowConfig:
    return FlowConfig(
        agent_type="greeter_agent",
        flow_type="main",
        version=1,
        description="A minimal three-step greeting flow.",
        steps=[
            StepConfig(id="step_greet", type="greet"),
            StepConfig(id="step_validate", type="validate"),
            StepConfig(id="step_summarise", type="summarise"),
        ],
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    registry = build_registry()
    flow_config = build_flow_config()

    print(f"Building flow: {flow_config.agent_type}.{flow_config.flow_type}")
    print(f"  Steps ({flow_config.step_count()}): {[s.id for s in flow_config.steps]}")

    builder = FlowGraphBuilder(registry=registry)
    compiled_graph = builder.build(flow_config)

    initial_state = {"name": "Alice"}
    print(f"\nInvoking graph with state: {initial_state}")
    result = compiled_graph.invoke(initial_state)

    print(f"\nFinal state keys: {sorted(result.keys())}")
    assert result.get("status") == "completed", "Expected status='completed'"
    assert result.get("validated") is True, "Expected validated=True"
    print("\nDone — flow_graph_example exited successfully.")


if __name__ == "__main__":
    main()

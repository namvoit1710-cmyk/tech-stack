"""subgraph_example.py — Subgraph composition with AgentGraphBuilder.

This example demonstrates both supported subgraph integration patterns:

1. **Shared state keys** — parent and subgraph share the same state schema.
   Use ``AgentGraphBuilder.add_subgraph()`` to embed a compiled subgraph
   directly as a node.

2. **Different state schemas** — parent and subgraph have different schemas
   and require explicit state mapping.  Use
   ``AgentGraphBuilder.add_mapped_subgraph()`` together with ``map_input``
   and ``map_output`` callables to translate between the two schemas.

All imports come from ``agent_sdk``; no direct ``langgraph`` imports are
required.

Usage::

    python examples/subgraph_example.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_sdk import END, AgentGraphBuilder, StateGraph

# ---------------------------------------------------------------------------
# Pattern 1: shared state keys → add_subgraph()
# ---------------------------------------------------------------------------


def _build_shared_subgraph():
    """Build a subgraph that shares state keys with its parent.

    Internal topology::

        [preprocess] → [transform] → END
    """

    def preprocess(state: dict) -> dict:
        message = state.get("message", "")
        return {"preprocessed": message.strip()}

    def transform(state: dict) -> dict:
        preprocessed = state.get("preprocessed", "")
        return {"result": f"processed: {preprocessed}"}

    sub = StateGraph(dict)
    sub.add_node("preprocess", preprocess)
    sub.add_node("transform", transform)
    sub.set_entry_point("preprocess")
    sub.add_edge("preprocess", "transform")
    sub.add_edge("transform", END)
    return sub.compile()


def build_shared_state_graph():
    """Build a parent graph using ``add_subgraph()`` (shared state keys).

    Parent topology::

        [prepare] → [sub_processor] → [finalise] → END

    Returns:
        A compiled LangGraph StateGraph.
    """
    sub = _build_shared_subgraph()

    def prepare(state: dict) -> dict:
        return {"message": state.get("input", "hello world")}

    def finalise(state: dict) -> dict:
        return {"output": state.get("result", "")}

    builder = (
        AgentGraphBuilder(state_schema=dict)
        .add_node("prepare", prepare)
        .add_subgraph("sub_processor", sub)
        .add_node("finalise", finalise)
        .set_entry_point("prepare")
        .add_edge("prepare", "sub_processor")
        .add_edge("sub_processor", "finalise")
        .add_edge("finalise", END)
    )
    return builder.compile()


# ---------------------------------------------------------------------------
# Pattern 2: different state schemas → add_mapped_subgraph()
# ---------------------------------------------------------------------------


def _build_mapped_subgraph():
    """Build a subgraph with its own internal state schema.

    Internal topology::

        [process] → END

    The subgraph operates on ``{"payload": str}`` and produces
    ``{"mapped_result": str}``.
    """

    def process(state: dict) -> dict:
        payload = state.get("payload", "")
        return {"mapped_result": f"mapped: {payload}"}

    sub = StateGraph(dict)
    sub.add_node("process", process)
    sub.set_entry_point("process")
    sub.add_edge("process", END)
    return sub.compile()


def build_mapped_subgraph_graph():
    """Build a parent graph using ``add_mapped_subgraph()`` (different schemas).

    Parent topology::

        [prepare] → [sub_processor] → [finalise] → END

    ``map_input`` converts parent state to subgraph input.
    ``map_output`` merges subgraph output back into parent state.

    Returns:
        A compiled LangGraph StateGraph.
    """
    sub = _build_mapped_subgraph()

    def prepare(state: dict) -> dict:
        return {"raw_input": state.get("input", "hello world")}

    def finalise(state: dict) -> dict:
        return {"output": state.get("result", "")}

    def map_input(state: dict) -> dict:
        return {"payload": state.get("raw_input", "").strip()}

    def map_output(subgraph_out: dict, parent_state: dict) -> dict:
        return {"result": subgraph_out.get("mapped_result", "")}

    builder = (
        AgentGraphBuilder(state_schema=dict)
        .add_node("prepare", prepare)
        .add_mapped_subgraph("sub_processor", sub, map_input, map_output)
        .add_node("finalise", finalise)
        .set_entry_point("prepare")
        .add_edge("prepare", "sub_processor")
        .add_edge("sub_processor", "finalise")
        .add_edge("finalise", END)
    )
    return builder.compile()


async def main() -> None:
    shared_graph = build_shared_state_graph()
    shared_result = shared_graph.invoke({"input": "  hello subgraph  "})
    print("shared_state_output:", shared_result.get("output"))

    mapped_graph = build_mapped_subgraph_graph()
    mapped_result = await mapped_graph.ainvoke({"input": "hello mapped subgraph"})
    print("mapped_state_output:", mapped_result.get("output"))


if __name__ == "__main__":
    asyncio.run(main())

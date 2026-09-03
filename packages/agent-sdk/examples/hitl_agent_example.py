"""hitl_agent_example.py — canonical human-in-the-loop (HITL) agent example.

This example demonstrates how to build an agent that pauses for human approval
using the SDK's HITL support.  Two patterns are shown:

1. **Runtime interrupt** (``interrupt()``): the graph node calls
   ``interrupt(value)`` at runtime to pause and return an interrupt payload
   to the caller.  Execution resumes when the caller POSTs an approval to
   the ``/resume`` endpoint.

2. **Compile-time interrupt_before** (``interrupt_before``): the graph is
   compiled with ``interrupt_before=["<node_name>"]`` so LangGraph
   automatically pauses before that node without requiring node-level code
   changes.

The example also demonstrates **helper-emitted workflow events** using
``emit_workflow_event``.  Inside a running workflow, any node can publish
a typed ``WorkflowEvent`` to the configured message bus without needing a
direct reference to the publisher.  The helper is a no-op when no active
``workflow_event_scope`` is present, so it is safe to call unconditionally.

Key SDK symbols used (no direct langgraph imports needed):
    ``AgentGraphBuilder``       — wraps LangGraph StateGraph
    ``interrupt``               — re-exported langgraph.types.interrupt()
    ``build_app_container``     — wires all SDK dependencies including
                                  ResumeAgentUseCase (exposed as /resume)
    ``run_agent``               — starts the FastAPI server
    ``HitlInterruptPayload``    — domain entity in the interrupted response
    ``HitlResumeCommand``       — domain entity for the resume request
    ``emit_workflow_event``     — contextvar helper: publish a WorkflowEvent
                                  from inside any node without a publisher ref
    ``WorkflowEvent``           — base event dataclass
    ``EVENT_NODE_COMPLETED``    — event type constant

Required environment variables:
    OPENAI_API_KEY      — OpenAI API key (required for LLM nodes)
    MESSAGING_MODE      — "mock" (default, no-op), "local" (Kafka), or "sap";
                          takes precedence over INFRA_MODE when set
    INFRA_MODE          — fallback transport selector when MESSAGING_MODE is unset
    PUSH_GATEWAY_URL    — optional REST fan-out URL; when set, terminal workflow
                          events are also delivered via HTTP push alongside broker

Flow diagram::

    [start] → [prepare_action] → [request_approval] → [execute_action] → [END]
                                         ↑ interrupt()
                                                              ↓ emit_workflow_event()

    On approve:  POST /resume {"thread_id": "...", "resume_value": "approved"}
    On reject:   POST /resume {"thread_id": "...", "resume_value": "rejected"}

Usage::

    python hitl_agent_example.py

    # Execute the agent
    curl -X POST http://localhost:36000/api/v1/execute \\
         -H 'Content-Type: application/json' \\
         -d '{"message": "transfer $100 to account #42", "conv_id": "conv-1"}'

    # If interrupted, resume with approval
    curl -X POST http://localhost:36000/api/v1/resume \\
         -H 'Content-Type: application/json' \\
         -d '{"thread_id": "conv-1", "resume_value": "approved"}'
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_sdk import (
    EVENT_NODE_COMPLETED,
    AgentGraphBuilder,
    WorkflowEvent,
    create_checkpointer,
    emit_workflow_event,
    interrupt,
    run_agent,
    settings,
)

# ---------------------------------------------------------------------------
# Agent state definition
# ---------------------------------------------------------------------------


class ApprovalState(dict):
    """Simple dict-based state.  In real agents use a TypedDict or dataclass."""


# ---------------------------------------------------------------------------
# Pattern 1: Runtime interrupt using interrupt()
#
# The node explicitly calls interrupt() to pause execution and surface the
# interrupt payload to the caller.  The return value of interrupt() becomes
# the human's response when execution is resumed.
# ---------------------------------------------------------------------------


def prepare_action(state: dict, deps: dict) -> dict:
    """Extract and validate the requested action from the user message."""
    message = state.get("message", "")
    # In a real agent this could involve LLM parsing
    return {"action": message, "status": "pending"}


def request_approval(state: dict, deps: dict) -> dict:
    """Pause execution and ask a human to approve or reject the action."""
    action = state.get("action", "unknown action")
    # interrupt() pauses the graph here.  The value is surfaced in the
    # HitlInterruptPayload.value field returned to the API caller.
    # When resumed, the return value of interrupt() carries the human's answer.
    human_decision = interrupt(
        {
            "question": "Do you approve this action?",
            "action": action,
            "options": ["approved", "rejected"],
        }
    )
    return {"human_decision": human_decision}


async def execute_action(state: dict, deps: dict) -> dict:
    """Execute the action based on the human's decision.

    Demonstrates ``emit_workflow_event``: after determining the outcome
    we publish a ``NODE_COMPLETED`` event to the configured message bus.
    ``emit_workflow_event`` is a no-op when no active
    ``workflow_event_scope`` is present (e.g. in unit tests), so the
    node is safe to call unconditionally.
    """
    decision = state.get("human_decision", "rejected")
    action = state.get("action", "")
    if decision == "approved":
        result = {"message": f"Action executed: {action}", "status": "completed"}
    else:
        result = {"message": f"Action rejected: {action}", "status": "rejected"}

    event = WorkflowEvent(
        event_id="execute_action",
        event_type=EVENT_NODE_COMPLETED,
        message=result["message"],
        node_id="execute_action",
        data={"decision": decision, "action": action},
    )
    await emit_workflow_event(event, node_id="execute_action")

    return result


def build_hitl_graph_runtime_interrupt(checkpointer=None):
    """Build an agent graph using runtime interrupt() calls.

    The graph pauses at request_approval and waits for human input.
    A checkpointer is required for stateless resume to work — the graph
    saves state to the checkpoint store so it can be restored on resume.
    """
    builder = AgentGraphBuilder()
    builder.add_node("prepare_action", prepare_action)
    builder.add_node("request_approval", request_approval)
    builder.add_node("execute_action", execute_action)
    builder.set_entry_point("prepare_action")
    builder.add_edge("prepare_action", "request_approval")
    builder.add_edge("request_approval", "execute_action")
    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Pattern 2: Compile-time interrupt_before
#
# No node-level code changes needed.  Just pass interrupt_before=["<node>"]
# to compile() and LangGraph will pause before that node automatically.
# ---------------------------------------------------------------------------


def execute_sensitive_action(state: dict, deps: dict) -> dict:
    """Sensitive action that requires approval before running."""
    return {
        "message": f"Sensitive action completed: {state.get('action', '')}",
        "status": "completed",
    }


def build_hitl_graph_compile_time(checkpointer=None):
    """Build an agent graph using compile-time interrupt_before.

    LangGraph automatically pauses before execute_sensitive_action without
    requiring node-level interrupt() calls.
    """
    builder = AgentGraphBuilder()
    builder.add_node("prepare_action", prepare_action)
    builder.add_node("execute_sensitive_action", execute_sensitive_action)
    builder.set_entry_point("prepare_action")
    builder.add_edge("prepare_action", "execute_sensitive_action")
    # Pause before execute_sensitive_action — no node code change needed.
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["execute_sensitive_action"],
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the agent server with HITL support.

    The SDK's build_app_container wires ResumeAgentUseCase automatically when
    an agent_graph is provided, exposing the POST /resume endpoint.
    """
    # Use create_checkpointer(settings) for development (returns MemorySaver when
    # INFRA_MODE="mock").  In production pass a hana_connection_manager as well
    # to back checkpoints with a persistent SAP HANA store.
    checkpointer = create_checkpointer(settings)

    # Choose pattern 1 (runtime interrupt) or pattern 2 (compile-time)
    agent_graph = build_hitl_graph_runtime_interrupt(checkpointer=checkpointer)

    # run_agent starts the FastAPI server.
    # build_app_container is called internally and auto-wires ResumeAgentUseCase
    # when agent_graph is given.
    # Endpoints available:
    #   POST /api/v1/execute  — start execution (returns interrupt_payload if paused)
    #   POST /api/v1/resume   — resume paused execution with human decision
    run_agent(agent_graph=agent_graph)


if __name__ == "__main__":
    main()

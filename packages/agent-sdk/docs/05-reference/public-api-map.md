# Public API Map

[← Reference index](README.md) | [Full API reference](api-reference.md)

This page groups the public `agent_sdk` surface by **what you are trying to do**, not by internal module structure. Use it as a quick lookup before reaching for the full API reference.

---

## I want to build a simple tool-calling agent

**Start here:** `ToolAgentBuilder` + `make_openai_service` + `@tool`

```python
from agent_sdk import ToolAgentBuilder, make_openai_service, tool, run_agent

@tool
def search(query: str) -> dict:
    """Search for information."""
    return {"result": f"Results for: {query}"}

builder = ToolAgentBuilder(
    llm_service=make_openai_service(),
    tools=[search],
    system_prompt="You are a helpful assistant.",
)
graph = builder.compile()
run_agent(agent_graph=graph)
```

**Key symbols:** `ToolAgentBuilder`, `make_openai_service`, `tool`, `run_agent`, `ToolAgentState`

**Learn more:** [Path A — ToolAgentBuilder](../02-quickstart/choose-your-builder.md)

---

## I want to build a custom graph with full control

**Start here:** `AgentGraphBuilder` with explicit node and edge wiring.

```python
from agent_sdk import AgentGraphBuilder, ToolAgentState, END

builder = (
    AgentGraphBuilder(state_schema=ToolAgentState)
    .add_node("prepare", prepare_node)
    .add_node("agent", agent_node)
    .set_entry_point("prepare")
    .add_edge("prepare", "agent")
    .add_edge("agent", END)
)
graph = builder.compile()
```

**Key symbols:** `AgentGraphBuilder`, `ToolAgentState`, `AgentBaseState`, `END`, `ToolNode`, `tools_condition`, `interrupt`

**Learn more:** [Path B — AgentGraphBuilder](../02-quickstart/choose-your-builder.md)

---

## I want to add human-in-the-loop (HITL) pausing

**Start here:** Use `interrupt()` inside a node; pass a checkpointer to `compile()`.

```python
from agent_sdk import interrupt, create_checkpointer, settings

# Inside a graph node:
def approval_node(state):
    result = interrupt({"question": "Approve this action?", "data": state["data"]})
    return {"approved": result}

checkpointer = create_checkpointer(settings)
graph = builder.compile(checkpointer=checkpointer)
```

**Resume via API:** `POST /api/v1/resume` with `thread_id` and `resume_value`.

**Key symbols:** `interrupt`, `create_checkpointer`, `ResumeAgentUseCase`, `ResumeAgentInput`, `ResumeAgentOutput`, `HitlInterruptPayload`, `HitlResumeCommand`, `InterruptType`

**Learn more:** [HITL feature guide](../04-features/hitl.md)

---

## I want to call other agents from my agent (multi-agent orchestration)

**Start here:** `AgentDiscoveryService` + `default_tool_factory` for dynamic discovery, then prefer `AsyncAgentDelegator` for queue-first delivery. Use `AgentCallCoordinator` only for the legacy HTTP compatibility path.

```python
from agent_sdk import (
    AgentDiscoveryService, default_tool_factory, AgentCallCoordinator,
    ToolAgentBuilder, make_openai_service,
)

# Discover sub-agents from the registry and build tools
discovery = AgentDiscoveryService(
    registry=my_registry,
    exclude_agent_types=["my-supervisor"],
    tool_factory=default_tool_factory,
)
capabilities, tools = await discovery.discover()

# Build the supervisor
builder = ToolAgentBuilder(
    llm_service=make_openai_service(),
    tools=tools,
    system_prompt="You are a supervisor. Delegate to sub-agents.",
)
graph = builder.compile(checkpointer=checkpointer)

# Wire the coordinator in bootstrap.py
coordinator = AgentCallCoordinator(
    endpoint_resolver=my_resolver,
    resume_use_case=container["resume_agent"],
    http_client=httpx.AsyncClient(),
)
```

**Key symbols:** `AgentDiscoveryService`, `default_tool_factory`, `RemoteAgentTool`, `AsyncAgentDelegator`, `MessageReactionRouter`, `AgentCallCoordinator`, `AgentCapability`, `QueueMetadata`, `IAgentEndpointResolver`, `IAgentDelegator`

**Learn more:** [Remote agents feature guide](../04-features/remote-agents.md)

---

## I want to manage context budget (prevent context window overflow)

**Start here:** `ContextBudgetManager` + `ContextBudgetConfig`, passed to `ToolAgentBuilder`.

```python
from agent_sdk import ContextBudgetManager, ContextBudgetConfig, CompactionStrategy

budget = ContextBudgetManager(ContextBudgetConfig(
    max_total_tokens=16_000,
    compaction_strategy=CompactionStrategy.TIERED,
    preserve_business_payloads=True,
))

builder = ToolAgentBuilder(
    llm_service=make_openai_service(),
    tools=tools,
    context_budget=budget,
)
```

**Key symbols:** `ContextBudgetManager`, `ContextBudgetConfig`, `CompactionStrategy`, `PayloadType`

**Learn more:** [Context budget — API reference](api-reference.md#context-budget-management)

---

## I want to inject custom dependencies

**Start here:** Pass `extra_dependencies` to `build_app_container`.

```python
from agent_sdk import build_app_container

container = build_app_container(
    agent_graph=graph,
    extra_dependencies={
        "input_guard": MyInputGuard(),
        "output_guard": MyOutputGuard(),
        "agent_registry": MyCustomRegistry(),
    },
)
```

**Key interfaces:** `IInputGuard`, `IOutputGuard`, `ILogger`, `IMonitor`, `IAgentRegistry`, `IMessagePublisher`, `IMessageConsumer`, `IMessageDelivery`, `IWorkflowEventEmitter`, `ISharedStateRepository`

**Learn more:** [DI cookbook](../03-building-agents/dependency-injection-cookbook.md)

---

## I want to emit workflow events from graph nodes

**Start here:** `emit_workflow_event` + one of the typed event classes.

```python
from agent_sdk import emit_workflow_event, NodeStartedEvent

async def my_node(state, deps):
    await emit_workflow_event(NodeStartedEvent(), state=state, node_id="my_node", topic="events")
    return {"result": "done"}
```

**Key symbols:** `IWorkflowEventEmitter`, `WorkflowEventEmitter`, `emit_workflow_event`, `workflow_event_scope`, `serialize_event`, all `Event*` classes and `EVENT_*` constants

Queue-first / FE-specific symbols to look up here first:

- `ConversationMetadata`
- `ChatThinkingEvent`, `ChatResponseEvent`, `ChatDisabledEvent`, `ChatEnabledEvent`
- `OrchestrationEventType`, `AGENT_REQUEST_AGENT`, `AGENT_PLAN_ERROR`

**Learn more:** [Workflow events feature guide](../04-features/workflow-events.md)

---

## I want to add input/output validation guards

**Start here:** Implement `IInputGuard` or `IOutputGuard`, inject via `extra_dependencies`, and add the pre-built nodes to your graph.

```python
from agent_sdk import input_guard_node, output_guard_node, IInputGuard

class MyGuard(IInputGuard):
    def validate(self, message: str, **context) -> dict:
        if "forbidden" in message:
            return {"passed": False, "rejection_reason": "Forbidden content"}
        return {"passed": True}

container = build_app_container(
    agent_graph=graph,
    extra_dependencies={"input_guard": MyGuard()},
)
```

Add `input_guard_node` early in the graph and `output_guard_node` before `format_response`.

**Key symbols:** `input_guard_node`, `output_guard_node`, `error_handler_node`, `IInputGuard`, `IOutputGuard`

---

## I want to use a config-driven graph topology

**Start here:** `FlowGraphBuilder` + `FlowConfig` + `NodeTypeRegistry`.

**Key symbols:** `FlowGraphBuilder`, `FlowConfig`, `StepConfig`, `NodeTypeRegistry`

**Learn more:** [Path C — FlowGraphBuilder](../02-quickstart/choose-your-builder.md)

---

## I want to run the agent in CONSUMER (message queue) mode

**Start here:** Set `APP_MODE=CONSUMER` and configure `MESSAGING_MODE`.

```bash
APP_MODE=CONSUMER
MESSAGING_MODE=local   # or sap
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
```

The SDK calls `run_consumer_agent` automatically when `APP_MODE=CONSUMER`. `MessageReactionRouter` resumes `agent.response` envelopes and `AsyncAgentDelegator` publishes `AGENT_CALL` interrupts using queue metadata when those dependencies are wired.

**Key symbols:** `run_consumer_agent`, `AsyncAgentDelegator`, `IMessageConsumer`, `IMessagePublisher`, `IMessageDelivery`, `MessageReactionRouter`, `QueueMetadata`

Ack/retry/dead-letter behavior is part of the public queue-first surface:

- `IMessageDelivery.ack()`
- `IMessageDelivery.nack(requeue=True)`
- `IMessageDelivery.reject()`

### I want shared-state or resolver helpers for queue-first agents

**Key symbols:** `StateResolver`, `ensure_state_resolver`, `extract_state_snapshot`, `merge_state_snapshot`, `SharedStateDocument`, `SharedStateRecord`, `SharedStateLockInfo`, `HanaSharedStateRepository`

These helpers support queue retries, parent/sub-agent hand-off, and durable cross-message state without leaking broker or database clients into Layer 2 code.

### I want registry/runtime metadata for queue-first discovery

**Key symbols:** `AgentRegistration`, `AgentRuntimeConfig`, `ExecutionPolicy`, `QueueMetadata`, `RegistryEndpointResolver`

These contracts expose `AGENT_KIND`, `IS_PUBLISHED`, execution policy, and queue metadata through typed models instead of ad-hoc dict parsing.

**Learn more:** [Transports feature guide](../04-features/transports.md), [Configuration](configuration.md)

---

## I want to deploy on SAP BTP (Cloud Foundry)

**Key configuration:**

```bash
GET_FROM_VCAP=true
INFRA_MODE=production
APP_MODE=SERVER
```

**Key symbols:** `get_hana_credentials`, `HanaCredentials`, `HanaConnectionManager`, `HanaCheckpointSaver`, `create_checkpointer`

**Learn more:** [Configuration](configuration.md)

---

## Quick symbol lookup

| I need… | Symbol |
|---|---|
| Start the agent | `run_agent` |
| Assemble DI container | `build_app_container` |
| Build FastAPI app | `create_agent_app` |
| Simplest graph builder | `ToolAgentBuilder` |
| Full graph control | `AgentGraphBuilder` |
| Config-driven graph | `FlowGraphBuilder` |
| Decorate a tool | `tool` |
| Create OpenAI service | `make_openai_service` |
| Pause graph for human input | `interrupt` |
| Resume from HITL | `ResumeAgentUseCase` |
| Call another agent | `RemoteAgentTool`, `AgentCallCoordinator` |
| Discover sub-agents | `AgentDiscoveryService`, `default_tool_factory` |
| Manage context budget | `ContextBudgetManager` |
| Guard input/output | `input_guard_node`, `output_guard_node` |
| Emit workflow events | `emit_workflow_event`, `WorkflowEventEmitter` |
| Persist checkpoints | `create_checkpointer` |
| Read settings | `settings`, `Settings` |
| Custom DI injection | `build_app_container(extra_dependencies={...})` |

---

[← Reference index](README.md) | [Full API reference](api-reference.md) | [Runtime and container reference](runtime-and-container-reference.md)

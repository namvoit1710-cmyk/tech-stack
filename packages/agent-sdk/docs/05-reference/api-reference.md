# API Reference

[← Reference index](README.md) | [Docs home](../README.md)

All public symbols can be imported directly from `agent_sdk`. Only names in `agent_sdk.__all__` are guaranteed as stable top-level imports.

```python
from agent_sdk import ToolAgentBuilder, run_agent, AgentSDKError
```

---

## Contents

- [Builders](#builders)
- [Graph helpers and state](#graph-helpers-and-state)
- [Middleware and guard nodes](#middleware-and-guard-nodes)
- [LLM services](#llm-services)
- [Multi-agent coordination](#multi-agent-coordination)
- [Context budget management](#context-budget-management)
- [Checkpointing](#checkpointing)
- [Request / response types](#request--response-types)
- [HITL types](#hitl-types)
- [Workflow events](#workflow-events)
- [Infrastructure services](#infrastructure-services)
- [Interfaces (DI protocols)](#interfaces-di-protocols)
- [Domain entities](#domain-entities)
- [Exceptions](#exceptions)
- [LangGraph re-exports](#langgraph-re-exports)

For runtime entry points (`run_agent`, `build_app_container`, `create_agent_app`, `run_consumer_agent`), see [runtime-and-container-reference.md](runtime-and-container-reference.md).

For settings and environment variables, see [configuration.md](configuration.md).

---

## Builders

### `ToolAgentBuilder`

```python
from agent_sdk import ToolAgentBuilder, ContextBudgetManager, ContextBudgetConfig, CompactionStrategy, make_openai_service, tool

@tool
def my_tool(x: str) -> dict:
    """Describe the tool."""
    return {"result": x}

builder = ToolAgentBuilder(
    llm_service=make_openai_service(),
    tools=[my_tool],
    system_prompt="You are a helpful assistant.",
    context_budget=ContextBudgetManager(ContextBudgetConfig(
        max_total_tokens=16_000,
        compaction_strategy=CompactionStrategy.TIERED,
    )),
    system_reminder="Always include the file_id in your response.",
    system_reminder_threshold=6,
)
graph = builder.compile()
```

The highest-level builder. Wraps `AgentGraphBuilder` to produce a standard tool-calling agent with a `prepare → agent → tools_condition → tools/format_response` topology.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `llm_service` | `ILLMService \| None` | `None` | SDK service object (preferred) |
| `llm` | `Any \| None` | `None` | Explicit LangChain chat model (mutually exclusive with `llm_service`) |
| `tools` | `list[BaseTool]` | — | List of tools for the agent |
| `system_prompt` | `str` | `""` | System prompt prepended to every conversation |
| `pre_nodes` | `list[tuple[str, Callable]] \| None` | `None` | Nodes inserted before `prepare_messages` |
| `post_nodes` | `list[tuple[str, Callable]] \| None` | `None` | Nodes inserted after `format_response` |
| `context_budget` | `ContextBudgetManager \| None` | `None` | When provided, `compact()` is called automatically before each LLM invocation |
| `system_reminder` | `str \| None` | `None` | Short instruction re-injected at the end of the message list after `system_reminder_threshold` messages |
| `system_reminder_threshold` | `int` | `6` | Number of messages after which `system_reminder` is appended |

### `AgentGraphBuilder`

```python
from agent_sdk import AgentGraphBuilder, ToolAgentState, END, tools_condition

builder = (
    AgentGraphBuilder(state_schema=ToolAgentState)
    .add_tools(tools)
    .add_node("prepare", prepare)
    .add_node("agent", agent_node)
    .add_tool_node("tools")
    .add_node("format_response", format_node)
    .set_entry_point("prepare")
    .add_edge("prepare", "agent")
    .add_tools_condition("agent", tools_node="tools", next_node="format_response")
    .add_edge("tools", "agent")
    .add_edge("format_response", END)
)
compiled = builder.compile(checkpointer=None, interrupt_before=[])
```

A fluent wrapper around LangGraph's `StateGraph`. Supports both sync and async node callables; prefer `async def` for I/O-bound work.

**Methods:**

| Method | Signature | Description |
|---|---|---|
| `add_node` | `(name, fn)` | Add a node. `fn` accepts `(state)` or `(state, deps)`. |
| `add_node_if` | `(condition, name, fn)` | Add a node only when `condition` is `True`. No-op when `False`. |
| `set_entry_point` | `(name)` | Set the graph entry node. |
| `add_edge` | `(from_node, to_node)` | Add a directed edge. |
| `add_conditional_edges` | `(from_node, router_fn, path_map)` | Add a conditional edge. `router_fn(state) -> str`. |
| `add_tools` | `(tools)` | Store the tools list for `add_tool_node` and `add_tools_condition`. |
| `add_tool_node` | `(name="tools")` | Add a LangGraph `ToolNode` for the stored tools list. |
| `add_tools_condition` | `(from_node, tools_node="tools", next_node=END)` | Route to tool node when last message has tool calls, otherwise to `next_node`. |
| `add_subgraph` | `(name, compiled_graph)` | Add a pre-compiled LangGraph graph as a node. |
| `add_mapped_subgraph` | `(name, compiled_graph, map_input, map_output)` | Add a subgraph with explicit input/output mapping. |
| `compile` | `(checkpointer=None, interrupt_before=None, interrupt_after=None)` | Compile and return the `CompiledGraph`. |

**Automatic node-event wrapping:** Nodes with `fn(state, deps)` signature are automatically wrapped to inject `deps` from the container and emit `NodeStartedEvent` / `NodeCompletedEvent`. Single-parameter nodes `fn(state)` and subgraph nodes are not wrapped.

### `FlowGraphBuilder`

A declarative builder for config-driven graphs. Supports sequential steps, boolean `condition` branching, and string-based `router` dispatch. Use when graph topology is defined in data (`FlowConfig`) rather than handwritten node wiring.

### `StepConfig`

```python
from agent_sdk import StepConfig
```

Dataclass representing a single step in a `FlowConfig`.

### `NodeTypeRegistry`

```python
from agent_sdk import NodeTypeRegistry
```

Registry for mapping step types to node functions in a `FlowGraphBuilder`.

---

## Graph helpers and state

### `ToolAgentState`

```python
from agent_sdk import ToolAgentState
```

A `TypedDict` / `AgentBaseState` subclass pre-configured for tool-calling agents. Contains `messages`, `message`, `transport_state`, `formatted_response`, and `tool_results` fields.

### `AgentBaseState`

```python
from agent_sdk import AgentBaseState
```

Base `TypedDict` for all agent graph states.

### `format_response_node`

```python
from agent_sdk import format_response_node
```

Pre-built graph node that extracts the final response from `state["messages"]` and writes it to `state["formatted_response"]`.

### `tool`

```python
from agent_sdk import tool

@tool
def calculate(expression: str) -> dict:
    """Evaluate a math expression.

    Args:
        expression: A Python math expression string.
    """
    return {"result": eval(expression)}
```

Decorator that converts a plain function into a LangChain `BaseTool`. The docstring is used as the tool description. Re-exported from `langchain_core.tools.tool`.

---

## Middleware and guard nodes

### `input_guard_node`

Pre-built graph node `(state, deps) -> dict` that validates and sanitizes the incoming message before it reaches the LLM. Reads `deps["input_guard"]`; no-op if absent.

**State reads:** `state["message"]`, `state["user_id"]` (falls back to `"anonymous"`).

**State writes on success:**

| Key | Value |
|---|---|
| `input_guard_result` | `{"passed": True, "warnings": [...]}` |
| `message` | Sanitized message (only if `IInputGuard.validate` returns `sanitized_message`) |

**State writes on rejection:**

| Key | Value |
|---|---|
| `input_guard_result` | `{"passed": False, "rejection_reason": "..."}` |
| `error` | The rejection reason string |
| `error_code` | `"SDK_INPUT_001"` |

### `output_guard_node`

Pre-built graph node `(state, deps) -> dict` that validates and sanitizes the formatted response before it is returned. Reads `deps["output_guard"]`; no-op if absent.

**State reads:** `state["formatted_response"]`.

**State writes on success:**

| Key | Value |
|---|---|
| `output_guard_result` | `{"passed": True, "warnings": [...]}` |
| `formatted_response` | Sanitized response (only if `IOutputGuard.validate` returns `sanitized_response`) |

**State writes on rejection:**

| Key | Value |
|---|---|
| `output_guard_result` | `{"passed": False, "rejection_reason": "..."}` |
| `error` | The rejection reason string |
| `error_code` | `"SDK_OUTPUT_001"` |

### `error_handler_node`

Pre-built graph node `(state, deps) -> dict` that formats any error in state into a structured response. Reads `deps["logger"]` (optional) to emit a structured log entry.

**State reads:** `state["error"]`, `state["error_code"]`, `state["conv_id"]`.

**State writes:**

| Key | Value |
|---|---|
| `formatted_response` | `{"type": "error", "content": error_msg, "error_code": error_code, "conv_id": conv_id}` |
| `transport_state` | `"ERROR"` |

---

## LLM services

### `make_openai_service`

```python
from agent_sdk import make_openai_service

svc = make_openai_service()                            # reads OPENAI_API_KEY from env
svc = make_openai_service(api_key="sk-...", model="gpt-4o")
svc = make_openai_service(model_kwargs={"reasoning_effort": "medium"})
svc = make_openai_service(
    model_kwargs={"reasoning": {"effort": "medium"}}
)
```

Convenience factory for `OpenAIService`. Reads defaults from `settings` when arguments are omitted and forwards `model_kwargs` unchanged unless a key duplicates a first-class SDK option.

For standard OpenAI chat models, pass `model_kwargs={"reasoning_effort": "medium"}`. Responses API / Azure-style usage instead pairs `model_kwargs={"reasoning": {"effort": "medium"}}` with the corresponding provider/runtime path.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `api_key` | `str \| None` | `None` | Provider credential; falls back to `settings.OPENAI_API_KEY` inside `OpenAIService` |
| `model` | `str \| None` | `settings.LLM_MODEL` | Chat model name |
| `temperature` | `float \| None` | `settings.LLM_TEMPERATURE` | Sampling temperature |
| `model_kwargs` | `dict[str, Any] \| None` | `None` | Extra provider-specific kwargs forwarded to `init_chat_model(...)` |

### `OpenAIService`

```python
from agent_sdk import OpenAIService

svc = OpenAIService(
    api_key="sk-...",
    model="gpt-4o-mini",
    temperature=0.01,
    model_kwargs={"reasoning_effort": "medium"},
)
llm = svc.get_chat_client()
```

Implements `ILLMService`. Builds a LangChain chat model via `init_chat_model(...)`.

`OpenAIService` also exposes `async get_chat_completion(system_prompt, user_prompt, json_mode=True)` as an additive convenience wrapper for direct-completion use cases. It returns the raw string content from the model response. When `json_mode=True` (the default), callers are still responsible for `json.loads(...)`, validation, and any retry handling they need.

**Constructor parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `api_key` | `str \| None` | `None` | OpenAI API key. Falls back to `settings.OPENAI_API_KEY`; omitted if neither is set. |
| `model` | `str` | `"gpt-4o-mini"` | Chat model name |
| `temperature` | `float` | `0.01` | Sampling temperature |
| `provider` | `str \| None` | `settings.LLM_PROVIDER` | Value forwarded as `model_provider` |
| `timeout` | `float \| None` | `settings.LLM_TIMEOUT` | Provider timeout |
| `max_tokens` | `int \| None` | `settings.LLM_MAX_TOKENS` | Provider max token limit |
| `max_retries` | `int` | `settings.LLM_MAX_RETRIES` | Provider retry count |
| `model_kwargs` | `dict[str, Any] \| None` | `settings.LLM_MODEL_KWARGS` | Extra provider-specific kwargs forwarded to `init_chat_model(...)` unchanged unless they duplicate `model_provider`, `temperature`, `timeout`, `max_tokens`, or `max_retries` |

**Methods:**

| Method | Returns | Description |
|---|---|---|
| `get_chat_client()` | `Any` | Returns a cached LangChain chat model created with `init_chat_model(...)` |
| `get_chat_completion(system_prompt, user_prompt, json_mode=True)` | `str` | **Async.** Convenience wrapper for direct-completion flows. Defaults `json_mode=True`, requests JSON-object formatting when enabled, and returns the raw response string for caller-owned parsing/retry logic. |

---

## Multi-agent coordination

### `RemoteAgentTool`

```python
from agent_sdk import RemoteAgentTool

tool = RemoteAgentTool(
    remote_agent_type="data-processor",
    registry=registry_instance,
    name="data_processor",
    description="Delegates processing to the data-processor agent.",
    resolve_ttl_seconds=300,
)
```

A LangChain `BaseTool` that resolves a remote agent by `agent_type` and issues an `interrupt()`. Async-only — `_run` raises `NotImplementedError`.

`resolve_ttl_seconds=0.0` means cache forever. That is backward compatible, but for long-lived supervisor services a finite TTL is safer because downstream agents may restart and receive new `agent_id` values.

### `AgentCallCoordinator`

```python
from agent_sdk import AgentCallCoordinator, AgentCapability

coordinator = AgentCallCoordinator(
    endpoint_resolver=resolver,
    resume_use_case=resume_uc,
    http_client=httpx.AsyncClient(),
    timeout=300.0,
    max_chain_depth=5,
    capabilities={
        "file-processor-agent": AgentCapability(
            agent_type="file-processor-agent",
            name="File Processor",
            description="Processes files.",
            input_schema={},
            output_schema={},
            required_parameters=["file_id"],
            timeout_seconds=120.0,
        ),
    },
    max_retries=3,
    retry_backoff_seconds=1.0,
)
result = await coordinator.execute_with_auto_resume(execute_uc, request)
```

Handles the `AGENT_CALL` interrupt lifecycle automatically: validates input, resolves sub-agent URL, POSTs to sub-agent, resumes parent graph. Retries transient failures (timeouts, HTTP 502/503/504) with exponential backoff.

For a supervisor **server**, this coordinator is typically installed by overriding the SDK app container's default `execute_agent` use case. Building the graph alone is not enough.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `endpoint_resolver` | `IAgentEndpointResolver` | — | Maps `agent_id` → HTTP base URL |
| `resume_use_case` | `ResumeAgentUseCase` | — | Use case for the resume path |
| `http_client` | `httpx.AsyncClient` | — | Shared HTTP client |
| `timeout` | `float` | `300.0` | Global per-sub-agent call timeout (seconds) |
| `max_chain_depth` | `int` | `5` | Max nested `AGENT_CALL` loops |
| `capabilities` | `dict[str, AgentCapability] \| None` | `None` | Per-agent routing metadata |
| `max_retries` | `int` | `3` | Retry attempts for transient failures |
| `retry_backoff_seconds` | `float` | `1.0` | Base delay for exponential backoff |

### `IAgentEndpointResolver`

```python
from agent_sdk import IAgentEndpointResolver

class MyResolver(IAgentEndpointResolver):
    async def resolve_endpoint(self, agent_id: str) -> str:
        return f"http://{agent_id}.svc.cluster.local:36000"
```

Abstract port for resolving `agent_id` → HTTP base URL.

### `AgentCapability`

```python
from agent_sdk import AgentCapability

cap = AgentCapability(
    agent_type="file-processor-agent",
    name="File Processor",
    description="Processes files. Call when the user needs to transform a file.",
    input_schema={"type": "object", "properties": {"file_id": {"type": "string"}}},
    output_schema={},
    required_parameters=["file_id"],
    negative_examples=["user is asking a general question not about a file"],
)
```

Dataclass describing a sub-agent's routing contract.

| Field | Type | Default | Description |
|---|---|---|---|
| `agent_type` | `str` | — | Unique identifier |
| `name` | `str` | — | Display name |
| `description` | `str` | — | Routing description for the LLM |
| `input_schema` | `dict` | — | JSON Schema for the input |
| `output_schema` | `dict` | — | JSON Schema for the output |
| `required_parameters` | `list[str]` | `[]` | Validated before the HTTP call |
| `negative_examples` | `list[str]` | `[]` | "Do NOT call when" routing hints |
| `timeout_seconds` | `float` | `300.0` | Per-agent call timeout |
| `enabled` | `bool` | `True` | Excluded from discovery when `False` |

### `AgentDiscoveryService`

```python
from agent_sdk import AgentDiscoveryService, default_tool_factory

discovery = AgentDiscoveryService(
    registry=registry,
    exclude_agent_types=["supervisor"],
    tool_factory=default_tool_factory,
)
capabilities, tools = await discovery.discover(domain="finance")
```

Queries the service registry for active agents and builds `AgentCapability` objects. When `tool_factory` is provided, each capability is also converted into a `RemoteAgentTool`.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `registry` | `IAgentRegistry` | — | Registry client |
| `exclude_agent_types` | `list[str]` | `[]` | Agent types to skip (e.g., the supervisor itself) |
| `tool_factory` | `Callable \| None` | `None` | Converts a capability into a LangChain tool |

| Method | Returns | Description |
|---|---|---|
| `discover(domain=None)` | `tuple[list[AgentCapability], list[BaseTool]]` | Discovers agents, optionally filtered by domain |

### `default_tool_factory`

```python
from agent_sdk import default_tool_factory

tool = default_tool_factory(capability, registry)
# tool.name → "call_file_processor_agent"
```

Converts an `AgentCapability` and `IAgentRegistry` into a `RemoteAgentTool`. Derives the tool name by prepending `call_` and replacing hyphens with underscores. Appends `negative_examples` to the description as "Do NOT call this agent when: ..." lines.

---

## Context budget management

### `ContextBudgetConfig`

```python
from agent_sdk import ContextBudgetConfig, CompactionStrategy

config = ContextBudgetConfig(
    max_total_tokens=16_000,
    max_message_history=10,
    max_tool_result_tokens=2_000,
    compaction_strategy=CompactionStrategy.TIERED,
    compaction_threshold=0.8,
    aggressive_threshold=0.85,
    danger_threshold=0.95,
    preserve_business_payloads=True,
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `max_total_tokens` | `int` | `16_000` | Hard token limit for the model |
| `max_message_history` | `int` | `10` | Maximum messages to retain after compaction |
| `max_tool_result_tokens` | `int` | `2_000` | Individual tool results exceeding this count are truncated before any strategy runs |
| `compaction_strategy` | `CompactionStrategy` | `SELECTIVE` | Algorithm to apply when threshold is exceeded |
| `compaction_threshold` | `float` | `0.8` | Trigger compaction at this fraction of `max_total_tokens` |
| `aggressive_threshold` | `float` | `0.85` | (TIERED) Escalate to medium compaction at this fraction |
| `danger_threshold` | `float` | `0.95` | (TIERED) Escalate to heavy compaction at this fraction |
| `preserve_business_payloads` | `bool` | `True` | Never clear `BUSINESS_DATA`-marked messages |

### `ContextBudgetManager`

```python
from agent_sdk import ContextBudgetManager, ContextBudgetConfig, PayloadType

manager = ContextBudgetManager(config)
tokens = manager.count_tokens(messages)
compacted, warnings = await manager.compact(messages, payload_types={3: PayloadType.BUSINESS_DATA})
```

Pass to `ToolAgentBuilder(context_budget=manager)` to enable automatic compaction before each LLM invocation.

| Method | Returns | Description |
|---|---|---|
| `count_tokens(messages)` | `int` | Approximate token count using `tiktoken` |
| `compact(messages, *, payload_types)` | `tuple[list[dict], list[str]]` | **Async.** Returns `(compacted_messages, warnings)` |

**Constructor parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `config` | `ContextBudgetConfig` | — | Budget and strategy configuration |
| `llm_service` | `ILLMService \| None` | `None` | When set and strategy is `TIERED`, the medium tier uses LLM-based summarization |

### `CompactionStrategy`

```python
from agent_sdk import CompactionStrategy

CompactionStrategy.TOOL_RESULT_CLEAR  # clear old tool results (keep BUSINESS_DATA)
CompactionStrategy.SELECTIVE          # preserve BUSINESS_DATA; clear old tool results; keep last N messages
CompactionStrategy.HEAD_TAIL          # keep system prompt + last N messages; drop everything in between
CompactionStrategy.TIERED             # escalate automatically based on context pressure (recommended for production)
CompactionStrategy.NONE               # no compaction
```

| Strategy | When to use |
|---|---|
| `TOOL_RESULT_CLEAR` | Tool results are large but reasoning history is short |
| `SELECTIVE` | (default) Supervisor handles structured business data |
| `HEAD_TAIL` | No business payloads to protect; most aggressive |
| `TIERED` | Long-running loops where context pressure varies. Recommended for production. |
| `NONE` | During development or with very large context windows (128K+) |

### `PayloadType`

```python
from agent_sdk import PayloadType

PayloadType.BUSINESS_DATA  # structured data preserved verbatim during compaction
PayloadType.NARRATIVE      # free-text reasoning, eligible for compaction
PayloadType.ROUTING        # routing metadata
```

Mark tool messages containing structured business data (file IDs, content blobs) as `BUSINESS_DATA` to prevent them from being cleared or summarised. When using `default_tool_factory`, any tool whose name starts with `call_` is automatically classified as `BUSINESS_DATA`.

---

## Checkpointing

### `create_checkpointer`

```python
from agent_sdk import create_checkpointer, settings

checkpointer = create_checkpointer(settings)
graph = builder.compile(checkpointer=checkpointer)
```

Factory that returns `MemorySaver` when `INFRA_MODE="mock"` and `HanaCheckpointSaver` when a real HANA database is configured. Required for HITL resume and `AgentCallCoordinator`.

### `HanaCheckpointSaver`

```python
from agent_sdk import HanaCheckpointSaver
```

LangGraph-compatible checkpoint saver backed by SAP HANA. Created automatically by `create_checkpointer` in production mode.

---

## Request / response types

### `ExecuteAgentInput` / `ExecuteAgentOutput`

Input and output dataclasses for `POST /api/v1/execute`.

`ExecuteAgentInput` carries: `message` (required), `conv_id`, `session_id`, `user_id`, `tenant_id`, `source`, `correlation_id`, `parameters`, `context`, `reply_to`, `reply_topic`, `action`, `agent_type`, `intent`, `execution_context`, `context_snapshot`.

`ExecuteAgentOutput` carries: `message`, `status`, `session_id`, `agent_data`, `error`, `error_code`, `correlation_id`, `duration_ms`, `interrupted`, `interrupt_payload`. When `interrupted=True`, `interrupt_payload` contains a `HitlInterruptPayload`.

**Terminal output compatibility:**

- Preferred graph output: `formatted_response`
- Supported fallback: `agent_result`
- Additional fallback: top-level `agent_data` + `message` + `status`
- Error fallback: top-level `error` / `error_code`

When a custom graph returns `agent_result` without `formatted_response`, the SDK copies the full `agent_result` dict into `ExecuteAgentOutput.agent_data`, derives `message` from `content` (or `message`), and derives `status` from `status` (or `success`). This allows custom graphs to return domain payloads without forcing a dedicated format-response node.

### `ResumeAgentInput` / `ResumeAgentOutput`

Input and output for `POST /api/v1/resume`.

`ResumeAgentInput` carries: `thread_id`, `resume_value`, `interrupt_id` (optional).

`ResumeAgentOutput` carries: `message`, `status`, `data`, `agent_data`, `error`, `error_code`, `correlation_id`, `duration_ms`, `interrupted`, `interrupt_payload`.

`ResumeAgentInput.interrupt_id` uses targeted LangGraph resume only when a real interrupt ID is present. `None` or `""` means scalar resume (`Command(resume=resume_value)`).

`ResumeAgentOutput` follows the same terminal-output compatibility rules as execute. When `formatted_response["data"]` exists, it is preserved in `ResumeAgentOutput.data`; structured custom-graph payloads from `agent_result` are exposed via `ResumeAgentOutput.agent_data`.

### `ResumeAgentUseCase`

```python
from agent_sdk import ResumeAgentUseCase
```

Handles HITL resume. Wired automatically into the container by `build_app_container` when `agent_graph` is provided.

---

## HITL types

### `HitlInterruptPayload`

Returned in `ExecuteAgentOutput.interrupt_payload` when the graph is paused. Contains `thread_id`, `interrupt_id`, `value`, `type` (`InterruptType`), `message`, `tenant_id`, `user_id`, `conv_id`, and `metadata`.

### `HitlResumeCommand`

Domain entity written from `ResumeAgentInput`. Carries `thread_id` and `resume_value`.

### `InterruptType`

Enum of HITL interrupt types: `AGENT_CALL`, `GENERIC`.

### `TenantContext`

Dataclass with `tenant_id`, `user_id`, and `conv_id`. Threaded into graph state from `ExecuteAgentInput`.

---

## Workflow events

### Event constants

```python
from agent_sdk import (
    EVENT_WORKFLOW_STARTED, EVENT_UI_RENDER_REQUEST,
    EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST, EVENT_NODE_STARTED,
    EVENT_INPUT_VALIDATING, EVENT_NODE_WAITING_USER, EVENT_INPUT_UPDATED,
    EVENT_NODE_COMPLETED, EVENT_WORKFLOW_COMPLETED, EVENT_WORKFLOW_FAILED,
    EVENT_UNSUPPORTED_FEATURE, EVENT_NODE_UPDATED, EVENT_NODE_DATA_INITIALIZED,
    EVENT_AGENT_SELECTED, EVENT_TOOL_SELECTED,
)
```

### `WorkflowEvent`

Base dataclass for all workflow events. Fields: `event_id`, `event_type`, `message`, `conv_id`, `correlation_id`, `timestamp`, `workflow_id`, `node_id`, `data`.

### Event emission utilities

```python
from agent_sdk import (
    WorkflowEventEmitter,
    serialize_event,
    workflow_event_scope,
    emit_workflow_event,
)
```

- `WorkflowEventEmitter`: Default implementation of `IWorkflowEventEmitter`. Constructor: `WorkflowEventEmitter(publisher, logger=None, push_gateway_notifier=None)`. Publishes to the broker and, when `conv_id` is present, to the push gateway in parallel (both paths are best-effort).
- `serialize_event(event)`: Converts a `WorkflowEvent` to a dict.
- `workflow_event_scope(emitter, *, request, mode)`: Synchronous context manager (`with`, not `async with`) to set up the event emission scope.
- `emit_workflow_event(event, *, state, node_id, topic)`: Emits an event within the active scope.

### Typed event classes

| Class | Event type |
|---|---|
| `WorkflowStartedEvent` | `WORKFLOW_STARTED` |
| `UiRenderRequestEvent` | `UI_RENDER_REQUEST` |
| `WorkflowGuidelineRenderRequestEvent` | `WORKFLOW_GUIDELINE_RENDER_REQUEST` |
| `NodeStartedEvent` | `NODE_STARTED` |
| `InputValidatingEvent` | `INPUT_VALIDATING` |
| `NodeWaitingUserEvent` | `NODE_WAITING_USER` |
| `InputUpdatedEvent` | `INPUT_UPDATED` |
| `NodeCompletedEvent` | `NODE_COMPLETED` |
| `WorkflowCompletedEvent` | `WORKFLOW_COMPLETED` |
| `WorkflowFailedEvent` | `WORKFLOW_FAILED` |
| `UnsupportedFeatureEvent` | `UNSUPPORTED_FEATURE` |
| `NodeUpdatedEvent` | `NODE_UPDATED` |
| `NodeDataInitializedEvent` | `NODE_DATA_INITIALIZED` |
| `AgentSelectedEvent` | `AGENT_SELECTED` |
| `ToolSelectedEvent` | `TOOL_SELECTED` |

---

## Infrastructure services

### `HttpAgentRegistry`

```python
from agent_sdk import HttpAgentRegistry
```

Default implementation of `IAgentRegistry` that communicates with the registry service via HTTP.

### `AsyncAgentDelegator`

```python
from agent_sdk import AsyncAgentDelegator
```

Queue-native delegation helper that publishes `agent.request.agent` envelopes using registry `QueueMetadata`. It preserves correlation IDs, thread IDs, `reply_topic`, `reply_queue`, shared-state snapshots, and the downstream input payload so the parent agent can resume from the resulting `agent.response` envelope.

### `MessageReactionRouter`

```python
from agent_sdk import MessageReactionRouter
```

Broker-side delivery router used by CONSUMER mode. It dispatches `agent.request.agent` envelopes to `ExecuteAgentUseCase`, `agent.response` envelopes to `ResumeAgentUseCase`, and custom broker message types to injected handlers, then acknowledges the delivery after the handler completes.

### `get_hana_credentials` / `HanaCredentials`

```python
from agent_sdk import get_hana_credentials

creds = get_hana_credentials()
if creds:
    print(creds.host, creds.port, creds.schema)
```

Parse `VCAP_SERVICES` and return a `HanaCredentials` dataclass, or `None` if the variable is absent or malformed. See [configuration.md](configuration.md) for field details.

### `HanaConnectionManager`

```python
from agent_sdk import HanaConnectionManager
```

Manages connection pooling for SAP HANA databases using `hdbcli`.

---

## Interfaces (DI protocols)

Inject custom implementations via `build_app_container(extra_dependencies={...})`.

| Interface | Contract | Inject as |
|---|---|---|
| `ILogger` | `log(level, message, **context)` | `"logger"` |
| `IMonitor` | `track(metric, value)` | `"monitor"` |
| `IAgentRegistry` | `register`, `deregister`, `heartbeat`, `resolve_agent_id` | `"agent_registry"` |
| `IAgentPipeline` | `execute(input)`, `resume(command)` | — |
| `ILLMService` | `get_chat_client()` | `"llm_service"` |
| `IInputGuard` | `validate(message, **context) -> dict` | `"input_guard"` |
| `IOutputGuard` | `validate(response, **context) -> dict` | `"output_guard"` |
| `IMessagePublisher` | `publish(topic, message, key)` | `"publisher"` |
| `IMessageConsumer` | `start(handler)`, `stop()` | `"consumer"` |
| `IWorkflowEventEmitter` | `emit(event)` | `"workflow_event_emitter"` |
| `IAgentEndpointResolver` | `resolve_endpoint(agent_id) -> str` | passed to `AgentCallCoordinator` |

`OpenAIService` is commonly injected as `"openai_service"` when a use case or node wants the SDK's concrete convenience wrapper `get_chat_completion(...)` directly. Tool-calling flows such as `ToolAgentBuilder` continue to use the raw chat model via `llm` / `llm_service`.

**`IInputGuard.validate` return shape:**

| Key | Type | Required | Description |
|---|---|---|---|
| `passed` | `bool` | Yes | `True` if the input is acceptable |
| `warnings` | `list[str]` | No | Non-blocking notices |
| `sanitized_message` | `str` | No | Replacement message forwarded to the LLM |
| `rejection_reason` | `str` | When `passed=False` | Written to `state["error"]`; triggers error code `"SDK_INPUT_001"` |

**`IOutputGuard.validate` return shape:**

| Key | Type | Required | Description |
|---|---|---|---|
| `passed` | `bool` | Yes | `True` if the output is acceptable |
| `warnings` | `list[str]` | No | Non-blocking notices |
| `sanitized_response` | `dict` | No | Replacement response that overwrites `state["formatted_response"]` |
| `rejection_reason` | `str` | When `passed=False` | Written to `state["error"]`; triggers error code `"SDK_OUTPUT_001"` |

---

## Domain entities

| Symbol | Notes |
|---|---|
| `AgentInfo` | Metadata returned by `GET /api/v1/info` |
| `AgentRequest` | Internal representation of an incoming execute request |
| `AgentResponse` | Internal representation of an outgoing response |
| `AgentRegistration` | Registration payload sent to the registry on startup |
| `QueueMetadata` | Queue registration metadata (`queue_name`, `request_topic`, `reply_topic`, `delivery_hints`) |
| `AgentStatus` | Enum: `HEALTHY`, `DEGRADED`, `UNHEALTHY` |
| `TransportState` | Enum: `IDLE`, `PROCESSING`, `RECEIVED`, `COMPLETED`, `ERROR` |
| `NodeType` | Enum of node types |
| `AgentCallRequest` | Request payload for an `AGENT_CALL` interrupt (`agent_id`, `agent_type`, `input_payload`, `interrupt_id`, `thread_id`, `correlation_id`, `session_id`, `reply_topic`, `reply_queue`, `metadata`, `context_snapshot`) |
| `AgentCallResult` | Result payload returned from an `AGENT_CALL` interrupt |
| `FlowConfig` | Config for `FlowGraphBuilder` |
| `StepConfig` | Single step in a `FlowConfig` |

---

## Exceptions

All exceptions inherit from `AgentSDKError`.

| Exception | Raised when |
|---|---|
| `AgentSDKError` | Base class for all SDK errors |
| `GraphCompilationError` | `compile()` fails due to invalid graph topology |
| `NodeExecutionError` | A graph node raises an unhandled exception during execution |
| `RegistrationError` | The agent registry HTTP call fails or returns a non-200 status |
| `DependencyError` | A required dependency is missing from the container at injection time |

```python
from agent_sdk import AgentSDKError, GraphCompilationError, RegistrationError

try:
    await registry.register(reg)
except RegistrationError as e:
    logger.error("Registry unavailable: %s", e)
```

---

## LangGraph re-exports

| Symbol | Origin | Usage |
|---|---|---|
| `END` | `langgraph.graph` | Terminal node sentinel for `add_edge` |
| `StateGraph` | `langgraph.graph` | Direct LangGraph state graph (rarely needed) |
| `ToolNode` | `langgraph.prebuilt` | Pre-built node that routes tool calls |
| `tools_condition` | `langgraph.prebuilt` | Routing function for `add_conditional_edges` |
| `interrupt` | `langgraph.types` | Pause graph execution at a node |
| `HumanMessage` | `langchain_core.messages` | Represents a message from a human |
| `SystemMessage` | `langchain_core.messages` | Represents a system message |

---

[← Reference index](README.md) | [Runtime and container reference](runtime-and-container-reference.md) | [Configuration](configuration.md)

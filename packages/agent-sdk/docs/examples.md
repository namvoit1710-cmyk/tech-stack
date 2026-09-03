# Examples

> **This page is a compatibility stub.**
> The examples index and learning-stage roadmap have moved to [02 — Quick Start → Examples Roadmap](02-quickstart/examples-roadmap.md).

[← README](../README.md) | [Docs index](README.md)

---

## Where to go

| Goal | Go to |
|---|---|
| Find the right example for your stage | [02 — Quick Start → Examples Roadmap](02-quickstart/examples-roadmap.md) |
| Annotated walkthrough of a specific example | Continue reading below |

---

## Quick reference

| File / folder | Builder path | Key concepts |
|---|---|---|
| `examples/minimal_tool_agent.py` | Path A — `ToolAgentBuilder` | Lowest boilerplate; single-file tool agent — **start here** |
| `examples/convenient_tool_agent.py` | Path B — `AgentGraphBuilder` | Manual graph wiring with tool loop |
| `examples/hitl_agent_example.py` | HITL | `interrupt()`, `interrupt_before`, stateless resume, `emit_workflow_event` |
| `examples/tool_agent_example.py` | Path A — `ToolAgentBuilder` | Multi-tool agent with richer setup |
| `examples/subgraph_example.py` | Subgraphs — `AgentGraphBuilder` | Embedded compiled subgraph as a single node |
| `examples/echo_agent/` | Reference app | Multi-file project structure; messaging auto-wiring override |
| `examples/flow_graph_example.py` | Path C — `FlowGraphBuilder` | Declarative config-driven flow; `FlowConfig`, `StepConfig`, `NodeTypeRegistry` |
| `examples/remote_agent_example.py` | Remote agents — `RemoteAgentTool` | Delegate to a remote sub-agent via orchestrator interrupt; `--demo` dry-run |
| `examples/kafka_consumer_example.py` | CONSUMER mode | SAP Event Mesh-native consumer walkthrough; Kafka local compatibility; `reply_to`, `reply_queue`, `--demo` dry-run |
| `examples/queue_native_supervisor_example.py` | Queue-first supervisor | Registry `QueueMetadata`, `AsyncAgentDelegator`, `MessageReactionRouter`, `correlation_threads` |
| `examples/supervisor_agent_example.py` | HTTP compatibility supervisor — `AgentCallCoordinator` | Auto-handle AGENT_CALL interrupt cycles over HTTP; dynamic discovery with `AgentDiscoveryService` and `AgentCapability` |
| `examples/context_budget_example.py` | Context budget — `ContextBudgetManager` | Token-limit enforcement with selective/head-tail compaction; business payload preservation |

---

## `minimal_tool_agent.py` — Path A

**File:** `examples/minimal_tool_agent.py`

The canonical entry point for new agent authors. Shows the minimum code needed to define a tool, wire it into an agent, and start the HTTP server.

```python
from agent_sdk import ToolAgentBuilder, make_openai_service, run_agent, tool

@tool
def wait_task(wait_type: str, duration_seconds: int = 60) -> dict:
    """Execute a wait operation for a given duration."""
    return {"status": "COMPLETED", "wait_type": wait_type, ...}

def main() -> None:
    builder = ToolAgentBuilder(
        llm_service=make_openai_service(),
        tools=[wait_task],
        system_prompt="You are a workflow assistant.",
    )
    run_agent(agent_graph=builder.compile())
```

**What it demonstrates:**
- `@tool` decorator — converts a plain function into a LangChain `BaseTool`.
- `make_openai_service()` — reads `OPENAI_API_KEY` from the environment; no direct OpenAI import needed.
- `ToolAgentBuilder` — single call to build the full `prepare → agent → tools_condition → tools/format_response` graph.
- `run_agent` — starts the FastAPI/uvicorn server on `SERVER_PORT` (default 36000).

**Run it:**
```bash
OPENAI_API_KEY=sk-... python examples/minimal_tool_agent.py
```

**Read next:** [02 — Quick Start → First Agent](02-quickstart/first-agent.md), [Builders](04-features/builders.md)

---

## `convenient_tool_agent.py` — Path B

**File:** `examples/convenient_tool_agent.py`

Demonstrates how to build the same tool-calling topology as Path A using `AgentGraphBuilder` directly. Use this when you need custom nodes, additional edges, or a topology that `ToolAgentBuilder` does not cover.

**What it demonstrates:**
- `AgentGraphBuilder` fluent API — `.add_node()`, `.add_tools_condition()`, `.add_edge()`, `.compile()`.
- `ToolAgentState` — pre-built state schema for tool-calling agents.
- Re-exported LangGraph symbols (`END`, `tools_condition`) — no direct `langgraph` import needed.
- Inline `async def agent(state)` node — nodes can be sync or async; `async def` is preferred for I/O-bound work.

**Run it:**
```bash
OPENAI_API_KEY=sk-... python examples/convenient_tool_agent.py
```

**Read next:** [Builders](04-features/builders.md), [API Reference — AgentGraphBuilder](05-reference/api-reference.md#agentgraphbuilder)

---

## `hitl_agent_example.py` — Path C

**File:** `examples/hitl_agent_example.py`

Demonstrates Human-in-the-Loop (HITL) patterns. Two patterns are shown:

**Pattern 1 — Runtime `interrupt()`:** The node calls `interrupt(payload)` to pause the graph and return a `HitlInterruptPayload` to the API caller. Execution resumes when `POST /api/v1/resume` is called with `{"thread_id": "...", "resume_value": "approved"}`.

**Pattern 2 — Compile-time `interrupt_before`:** No node code change. The graph is compiled with `interrupt_before=["node_name"]` and LangGraph pauses automatically before that node.

**What it demonstrates:**
- `interrupt()` — re-exported `langgraph.types.interrupt`; calling it pauses the graph.
- `build_app_container(agent_graph=...)` — wires `ResumeAgentUseCase` and exposes `/api/v1/resume`.
- `create_checkpointer(settings)` — returns `MemorySaver` when `INFRA_MODE="mock"` (default); required for stateless resume.
- `HitlInterruptPayload` / `HitlResumeCommand` — domain types for the interrupt / resume payloads.
- `emit_workflow_event` — helper for emitting business events (e.g., `NODE_WAITING_USER`) from within a node.

**Known behavior:** A checkpointer is required for resume to work. If the graph is compiled without one, the state cannot be persisted and `POST /api/v1/resume` will fail to find the thread.

**Run it:**
```bash
python examples/hitl_agent_example.py
# Execute:
curl -X POST http://localhost:36000/api/v1/execute \
     -H 'Content-Type: application/json' \
     -d '{"message": "transfer $100 to account #42", "conv_id": "conv-1"}'
# Resume:
curl -X POST http://localhost:36000/api/v1/resume \
     -H 'Content-Type: application/json' \
     -d '{"thread_id": "conv-1", "resume_value": "approved"}'
```

**Read next:** [HITL guide](04-features/hitl.md), [Checkpointing](checkpointing.md), [Workflow Events](workflow-events.md)

---

## `tool_agent_example.py` — Path D

**File:** `examples/tool_agent_example.py`

A multi-tool agent using `ToolAgentBuilder`. Extends Path A with additional tools to show how the tool loop handles multiple callable tools.

**What it demonstrates:**
- Multiple `@tool`-decorated functions registered in `ToolAgentBuilder`.
- Using `tool` from `agent_sdk` without importing `langchain_core` directly.

**Read next:** [Builders](04-features/builders.md)

---

## `subgraph_example.py` — Subgraph composition

**File:** `examples/subgraph_example.py`

Demonstrates both supported subgraph integration patterns using `AgentGraphBuilder`.

**What it demonstrates:**
- `StateGraph` re-export from `agent_sdk` — build a subgraph without importing `langgraph` directly.
- **Pattern 1 — shared state keys:** `AgentGraphBuilder.add_subgraph(name, compiled_graph)` embeds a compiled graph as a node when the parent and subgraph share the same state schema.
- **Pattern 2 — different state schemas:** `AgentGraphBuilder.add_mapped_subgraph(name, compiled_graph, map_input, map_output)` wraps a compiled subgraph in an explicit state-mapping node.

**Run it:**
```bash
python examples/subgraph_example.py
```

Expected output:
```
shared_state_output: processed: hello subgraph
mapped_state_output: mapped: hello mapped subgraph
```

**Read next:** [Builders — Subgraph patterns](04-features/builders.md#choosing-a-subgraph-integration-pattern), [Builders — add_subgraph](04-features/builders.md#add_subgraph), [Builders — add_mapped_subgraph](04-features/builders.md#add_mapped_subgraph), [Builders — Subgraphs (FlowGraphBuilder)](04-features/builders.md#subgraphs-in-flowgraphbuilder)

---

## `echo_agent/` — Reference application

**Directory:** `examples/echo_agent/`

Unlike the single-file examples above, `echo_agent/` is a **multi-file reference application** that shows how a real-world agent project is structured. It now demonstrates the layered **direct-completion** path: a Layer 2 node receives `openai_service` via DI and calls `await openai_service.get_chat_completion(...)` directly. This example is intentionally separate from the SDK's canonical tool-calling path, which still uses `ToolAgentBuilder` with the raw `llm` / `llm_service` surface.

### File structure

```
examples/echo_agent/
├── main.py          — application entry point; creates and serves the FastAPI app
├── bootstrap.py     — agent-specific DI wiring; provides openai_service + local tools
└── app/
    ├── layer1_domain/
    │   └── echo_state.py                              — typed graph state
    ├── layer2_application/
    │   └── echo_nodes.py                              — direct-completion + HITL nodes
    └── layer4_frameworks/
        ├── config/
        │   └── app_config.py                          — agent-specific Settings subclass
        └── graph/
            └── echo_graph_builder.py                  — compiles the layered AgentGraphBuilder flow
```

### What it demonstrates

- **`main.py`** — how to call `create_agent_app(container)` and start the server with uvicorn. The app is created once at module level so it can be used by ASGI servers.
- **`bootstrap.py`** — the recommended pattern for agent-specific DI wiring: create domain-specific dependencies (checkpointer, optional HANA connection, `openai_service`, local tools), then delegate to the SDK's `build_app_container` via `extra_dependencies`. The SDK auto-wires `publisher` (and `consumer` when `APP_MODE != SERVER`) from `MESSAGING_MODE` unless overridden — `bootstrap.py` does **not** need to inject a publisher manually.
- **Messaging auto-wiring** — omit `publisher` from `extra_dependencies` to use the SDK default (`MESSAGING_MODE`-driven). Pass a custom publisher to `extra_dependencies` to override.
- **HANA-optional setup** — `bootstrap.py` conditionally initializes `HanaConnectionManager` only when `HANA_HOST` is set, so the agent runs in development without any database.
- **`create_checkpointer(settings)`** — reads `INFRA_MODE` to decide between `MemorySaver` and `HanaCheckpointSaver`.
- **Layered direct completion** — `app/layer2_application/echo_nodes.py` calls `await openai_service.get_chat_completion(..., json_mode=False)` and returns the raw text result. This is the ergonomic wrapper path for direct prompt/response nodes.
- **Graph composition** — `app/layer4_frameworks/graph/echo_graph_builder.py` wires a simple `openai -> hitl -> END` flow with `AgentGraphBuilder`.
- **Layer-aware project layout** — source code is organised by clean-architecture layer (`layer2_application`, `layer4_frameworks`), mirroring the SDK's own internal structure.

### Key differences from the single-file examples

| | Single-file examples | `echo_agent/` |
|---|---|---|
| Entry point | `if __name__ == "__main__"` | `main.py` (importable module) |
| DI wiring | Inline in `main()` | Dedicated `bootstrap.py` |
| Settings | SDK defaults via `settings` | Subclassed `app_config.py` |
| LLM usage | Usually `ToolAgentBuilder` or manual `llm.ainvoke(...)` | Direct `openai_service.get_chat_completion(...)` in Layer 2 |
| Graph | Inline builder call | Dedicated `echo_graph_builder.py` with `build_echo_graph()` |
| HANA | Not used | Optional, conditionally initialised |
| Publisher | SDK default via `MESSAGING_MODE` | SDK default; override in `extra_dependencies` if needed |

**Read next:** [02 — Quick Start → First Agent](02-quickstart/first-agent.md), [Configuration](05-reference/configuration.md), [Architecture](architecture.md)

---

## `flow_graph_example.py` — declarative flow

**File:** `examples/flow_graph_example.py`

Demonstrates `FlowGraphBuilder`, the right builder when the workflow topology is driven by data (e.g. loaded from JSON/YAML or a database) rather than hard-coded graph edges.

**Description:** Compiles a three-step greeting flow (`greet → validate → summarise`) from a declarative `FlowConfig` and runs it against an in-memory state. No external services or environment variables are required.

**Key Components:**
- `FlowGraphBuilder` — compiles a `FlowConfig` into a runnable LangGraph `StateGraph`.
- `FlowConfig` / `StepConfig` — plain dataclasses describing the flow topology; can be deserialized from JSON/YAML in production.
- `NodeTypeRegistry` — maps step-type strings (e.g. `"greet"`) to Python callables; decouples flow definition from implementation.

**Run it:**
```bash
python examples/flow_graph_example.py
```

Expected output ends with `Done — flow_graph_example exited successfully.`

**Source:** [`examples/flow_graph_example.py`](../examples/flow_graph_example.py)

**Read next:** [Builders](04-features/builders.md)

---

## `remote_agent_example.py` — remote sub-agent delegation

**File:** `examples/remote_agent_example.py`

Demonstrates how to build an agent that delegates work to a remote sub-agent using `RemoteAgentTool`. When the LLM decides to call the remote tool, the tool resolves the target agent's concrete `agent_id` via `IAgentRegistry`, then calls `interrupt()` with an `AGENT_CALL` payload. The orchestrator intercepts the interrupt, forwards the call to the resolved agent, and resumes the graph with the result.

**Description:** Two usage paths are shown: production wiring with `HttpAgentRegistry` (requires `OPENAI_API_KEY` and `REGISTRY_URL`) and a self-contained `--demo` mode that runs without any external services.

**Key Components:**
- `RemoteAgentTool` — `BaseTool` subclass that triggers an `AGENT_CALL` interrupt payload.
- `IAgentRegistry` — interface for resolving `agent_type` → `agent_id`; backed by `HttpAgentRegistry` in production or `MockAgentRegistry` in tests.
- `ToolAgentBuilder` — wires the remote tool into the full tool-calling graph.
- `make_openai_service` / `run_agent` — standard server startup helpers.

**Run the demo (no env vars needed):**
```bash
python examples/remote_agent_example.py --demo
```

**Production usage (requires `OPENAI_API_KEY` + `REGISTRY_URL`):**
```bash
OPENAI_API_KEY=sk-... REGISTRY_URL=http://localhost:8080 \
    python examples/remote_agent_example.py
```

**Source:** [`examples/remote_agent_example.py`](../examples/remote_agent_example.py)

**Read next:** [Architecture](architecture.md), [Remote Agents](remote-agents.md)

---

## `kafka_consumer_example.py` — SAP Event Mesh-native consumer walkthrough

**File:** `examples/kafka_consumer_example.py`

Demonstrates the SAP Event Mesh-native consumer path with orchestrator-compatible request/response envelopes plus the queue-first delegation / correlated resume path. Kafka local compatibility remains available for development and local testing.

**What it demonstrates:**
- Full orchestrator request envelope: `reply_to`, `intent.text`, `execution_context`, `correlation_id`.
- Response envelope with top-level fields (`correlation_id`, `success`, `message`, `status`) plus nested `result` sub-object containing `session_id`, `interrupted`, `interrupt_payload`, `agent_data`, `error`, `error_code`.
- `reply_to`-based routing: final response goes to `reply_to`; progress events go to `reply_to.progress`.
- Queue-native delegation: `AsyncAgentDelegator` publishes `agent.request.agent`, records the parent thread in `correlation_threads`, and `MessageReactionRouter` resumes the parent when the correlated `agent.response` arrives.
- Transport vocabulary: `MESSAGING_MODE=mock` (default, no-op), `MESSAGING_MODE=sap` (SAP Event Mesh native), `MESSAGING_MODE=local` (Kafka local compatibility).
- `--demo` flag: self-contained dry-run using in-memory stubs — no Kafka or LLM required.

**Run the demo:**
```bash
python examples/kafka_consumer_example.py --demo
```

Expected output ends with `Demo passed — consumer pipeline verified end-to-end, including queue-native delegation and correlated resume.`

**Production usage:**
```bash
APP_MODE=CONSUMER \
MESSAGING_MODE=sap \
EVENT_MESH_TOKEN_URL=https://... \
EVENT_MESH_CLIENT_ID=... \
EVENT_MESH_CLIENT_SECRET=... \
EVENT_MESH_MESSAGING_URL=https://... \
EVENT_MESH_MANAGEMENT_URL=https://... \
EVENT_MESH_REQUEST_TOPIC=agent.request \
OPENAI_API_KEY=sk-... \
python examples/kafka_consumer_example.py

```

**Compatibility usage:**
```bash
APP_MODE=CONSUMER \
MESSAGING_MODE=local \
KAFKA_BOOTSTRAP_SERVERS=kafka:9092 \
KAFKA_REQUEST_TOPIC=agent.request \
KAFKA_GROUP_ID=my-agent-group \
OPENAI_API_KEY=sk-... \
python examples/kafka_consumer_example.py
```

**Read next:** [Transports](04-features/transports.md), [Remote Agents](04-features/remote-agents.md), [Workflow Events](04-features/workflow-events.md)

---

## `queue_native_supervisor_example.py` — queue-first multi-agent supervisor

**File:** `examples/queue_native_supervisor_example.py`

Demonstrates the queue-first counterpart to the HTTP supervisor flow. Instead of `AgentCallCoordinator`, the example uses `run_consumer_agent`, `AsyncAgentDelegator`, and `MessageReactionRouter` to publish delegated work and resume the paused supervisor thread when the downstream `agent.response` arrives.

**What it demonstrates:**
- Registry-driven `QueueMetadata` routing for downstream request and reply topics.
- Shared `correlation_threads` state between `AsyncAgentDelegator` and `MessageReactionRouter`.
- Queue-native `agent.request.agent` publish followed by correlated `agent.response` resume.
- In-memory `--demo` mode that shows the preferred broker-driven supervisor path without external services.

**Run the demo:**
```bash
python examples/queue_native_supervisor_example.py --demo
```

Expected output ends with `Demo passed — queue-first supervisor flow verified end-to-end.`

**Read next:** [Remote Agents](04-features/remote-agents.md), [Transports](04-features/transports.md)

---

## `supervisor_agent_example.py` — HTTP compatibility multi-agent supervisor

**File:** `examples/supervisor_agent_example.py`

Demonstrates how to build a supervisor agent that automatically handles `AGENT_CALL` interrupts using `AgentCallCoordinator`. This is the HTTP compatibility path: every sub-agent delegation becomes an HTTP call plus a resume loop. For the preferred broker-driven path, start with `queue_native_supervisor_example.py`.

**Description:** Shows two complementary patterns — `AgentCallCoordinator` for orchestrating the AGENT_CALL lifecycle and `AgentDiscoveryService`/`AgentCapability` for dynamically discovering sub-agents from the registry and generating `RemoteAgentTool` instances that the supervisor LLM uses to select sub-agents.

**Three graph-building approaches in one file (simplest → most control):**

1. **Static** (`build_supervisor_graph`) — Hardcodes a single `RemoteAgentTool`. Use this when you know exactly which agents exist and don't need runtime discovery.
2. **Dynamic** (`build_dynamic_supervisor_graph`) — Uses `AgentDiscoveryService` to query the registry, discover all active agents, and build tools automatically via `default_tool_factory`. Zero code changes needed when agents are added or removed. Uses `ToolAgentBuilder`.
3. **Custom topology** (`build_custom_agentgraphbuilder_supervisor`) — Uses `AgentGraphBuilder` directly with dynamic discovery, a guardrail node, and intent-based conditional routing. Use this when you need custom nodes (e.g., input validation, PII scrubbing) or branching logic that `ToolAgentBuilder`'s linear topology cannot express.

The third approach builds this graph:

```
[prepare] → [classify_intent] → conditional:
      ├── "delegate" → [call_llm] → tools_condition → [tools] ↩ [call_llm]
      │                                              → [format_response] → END
      └── "reject"  → [reject_response] → END
```

**Key components:**
- `AgentCallCoordinator` — wraps `ExecuteAgentUseCase.execute()` and automatically retries with `ResumeAgentUseCase` until no interrupt remains (or `max_chain_depth` is hit). Accepts `capabilities` for per-agent timeout and parameter validation.
- `IAgentEndpointResolver` — abstract port you implement to map `agent_id` → HTTP base URL. Implement once for your runtime (Kubernetes, Consul, static config).
- `AgentCapability` — dataclass describing a sub-agent: `agent_type`, `description`, `input_schema`, `output_schema`, `required_parameters`, `negative_examples`, `timeout_seconds`.
- `AgentDiscoveryService` — queries the registry for active agents, builds `AgentCapability` objects and optionally creates `RemoteAgentTool` instances via `default_tool_factory`.
- `default_tool_factory` — converts an `AgentCapability` into a `RemoteAgentTool` with an enriched description that includes negative routing examples.
- `RemoteAgentTool` — wires the sub-agent into `ToolAgentBuilder`; triggers `AGENT_CALL` interrupt when the LLM chooses to call it.
- `create_checkpointer(settings)` — required for the stateful HITL resume path.

**Important production note:** a supervisor server should not stop at `run_agent(agent_graph=...)`. The example now includes `build_supervisor_container()`, which shows the required production wiring:
1. build the graph
2. build the normal SDK app container
3. create `AgentCallCoordinator`
4. replace `container["execute_agent"]` with a coordinator-backed wrapper

Without that override, the supervisor emits `AGENT_CALL` interrupts but does not auto-resume them on the server side.

**Important reliability note:** for long-lived supervisors, do not leave `RemoteAgentTool` on infinite cached agent-id resolution unless you control downstream restarts. Prefer a finite `resolve_ttl_seconds` or explicit invalidation so agent restarts do not leave the supervisor calling stale IDs.

**Critical design rule:** Sub-agent responses must be passed through **verbatim**. Do not summarise or transform structured business payloads (file IDs, content blobs). `AgentCallCoordinator` passes the raw sub-agent JSON response as `resume_value`.

**Flow:**
```
User request
      ↓
AgentCallCoordinator.execute_with_auto_resume(execute_uc, request)
      ↓
ExecuteAgentUseCase.execute() → AGENT_CALL interrupt detected
      ↓  (auto-loop)
_call_sub_agent(agent_call) → POST sub-agent /api/v1/execute
      ↓
ResumeAgentUseCase.execute(resume_input)  ← verbatim sub-agent response
      ↓
Final ExecuteAgentOutput (no interrupt)
```

**Run the demo (no env vars needed):**
```bash
python examples/supervisor_agent_example.py --demo
```

**Production usage (requires `OPENAI_API_KEY` + `REGISTRY_URL`):**
```bash
OPENAI_API_KEY=sk-... REGISTRY_URL=http://localhost:8080 \
    python examples/supervisor_agent_example.py
```

**Source:** [`examples/supervisor_agent_example.py`](../examples/supervisor_agent_example.py)

**Read next:** [Architecture](architecture.md), [Multi-Agent Orchestration](remote-agents.md), [API Reference — AgentCallCoordinator](05-reference/api-reference.md#agentcallcoordinator)

---

## `context_budget_example.py` — context budget management

**File:** `examples/context_budget_example.py`

Demonstrates `ContextBudgetManager` — the tool for preventing context window overflow in supervisor agents that use small models (e.g. `gpt-4o-mini` with a 16K token limit). The example is self-contained: no external services or environment variables are required.

**What it covers:**

The example runs six demos, each demonstrating a different compaction strategy or feature:

| Demo | Strategy / Feature | What it shows |
|---|---|---|
| `demo_tool_result_truncation()` | `max_tool_result_tokens` | Pre-processing: truncate oversized tool results *before* any compaction runs. Prevents a single giant tool result from consuming the entire budget. |
| `demo_tool_result_clear()` | `TOOL_RESULT_CLEAR` | Clear old tool outputs while keeping all reasoning intact. Best when tool results are large but reasoning history is short. |
| `demo_selective()` | `SELECTIVE` (default) | Preserve `BUSINESS_DATA`-marked messages, clear old tool results, keep the last N messages. Recommended for supervisors that handle structured business data. |
| `demo_head_tail()` | `HEAD_TAIL` | Keep system prompt + last N messages, drop everything in between. Most aggressive — use when earlier context is truly disposable. |
| `demo_tiered()` | `TIERED` | Escalating compaction based on context pressure: light (80%) → medium (85%) → heavy (95%). Adapts automatically as the conversation grows. With `ILLMService`, medium tier uses LLM-based summarization. |
| `demo_system_reminder_config()` | `system_reminder` | Shows `ToolAgentBuilder` configuration for lost-in-the-middle mitigation. A short instruction is re-injected at the end of the message list. |

**Strategy comparison:**

| Strategy | Cost | When to use |
|---|---|---|
| `TOOL_RESULT_CLEAR` | Low | Tool results are large but reasoning history is short |
| `SELECTIVE` | Low | (default) Supervisor handles structured business data that must be preserved |
| `HEAD_TAIL` | Low | No business payloads; just keep system prompt + recent conversation |
| `TIERED` | Med | Long-running loops where context pressure varies. **Recommended for production** — "set-and-forget" escalation. |
| `NONE` | Free | Disable compaction entirely (development or 128K+ models) |

**Key concepts:**

- **`BUSINESS_DATA` vs `NARRATIVE`:** Mark messages containing structured payloads (file IDs, content blobs) as `PayloadType.BUSINESS_DATA` to guarantee they are never cleared, summarised, or dropped. Everything else is `NARRATIVE` and eligible for compaction.
- **`compact()` is async:** It was made async to support LLM-based summarization in `TIERED` mode. Use `await manager.compact(...)` or `asyncio.run(manager.compact(...))` from sync code.
- **`llm_service` parameter:** Pass an `ILLMService` to `ContextBudgetManager` to enable LLM-based summarization at the medium tier of `TIERED` compaction (instead of just dropping messages).
- **Lost-in-the-middle:** `ToolAgentBuilder` supports `system_reminder` and `system_reminder_threshold` to re-inject critical instructions at the end of long conversations.

**Key components:**
- `ContextBudgetConfig` — configuration dataclass. Key fields: `max_total_tokens`, `max_message_history`, `max_tool_result_tokens`, `compaction_strategy`, `compaction_threshold`, `aggressive_threshold` (TIERED), `danger_threshold` (TIERED), `preserve_business_payloads`.
- `ContextBudgetManager` — enforces the budget. Call `count_tokens(messages)` to inspect usage, `await compact(messages, payload_types=...)` to reduce the history. Optionally accepts `llm_service` for LLM-based summarization.
- `CompactionStrategy` — enum selecting the algorithm: `TOOL_RESULT_CLEAR`, `SELECTIVE`, `HEAD_TAIL`, `TIERED`, `NONE`.
- `PayloadType` — `BUSINESS_DATA`, `NARRATIVE`, `ROUTING`.
- `ToolAgentBuilder(context_budget=manager, system_reminder=..., system_reminder_threshold=...)` — wires budget enforcement + lost-in-the-middle mitigation into the agent.

**Run it:**
```bash
python examples/context_budget_example.py
```

Expected output ends with `Context budget example completed successfully.`

**Source:** [`examples/context_budget_example.py`](../examples/context_budget_example.py)

**Read next:** [API Reference — ContextBudgetManager](05-reference/api-reference.md#contextbudgetmanager)

---

→ Next: [Testing and Troubleshooting](testing-and-troubleshooting.md) | [Docs index](README.md)

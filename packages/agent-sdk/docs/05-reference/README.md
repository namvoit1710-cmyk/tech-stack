# 05 — Reference

[← Docs home](../README.md) | [SDK root](../../README.md)

---

## Pages in this section

| Page | What it covers |
|---|---|
| [api-reference.md](api-reference.md) | Every exported symbol grouped by category |
| [public-api-map.md](public-api-map.md) | "What symbol do I need?" grouped by developer job |
| [runtime-and-container-reference.md](runtime-and-container-reference.md) | `build_app_container`, `create_agent_app`, `run_agent`, `run_consumer_agent`, DI keys, transport assembly |
| [configuration.md](configuration.md) | Every environment variable with defaults and resolution rules |
| [testing-and-troubleshooting.md](testing-and-troubleshooting.md) | Running tests, debugging, and known limitations |

---

## Quick-start: what do I need?

**New to the SDK?** Start with the [public API map](public-api-map.md) — find the right symbol for your task without reading the full reference.

**Deploying an agent?** Read the [runtime and container reference](runtime-and-container-reference.md) for `build_app_container` parameters and DI container keys.

**Configuring the agent?** Read the [configuration reference](configuration.md) for every environment variable.

**Looking up a specific symbol?** Jump to the [full API reference](api-reference.md).

---

## API Surface Summary

All public symbols are exported from `agent_sdk` and listed in `agent_sdk.__all__`. Full signatures and descriptions: [api-reference.md](api-reference.md).

### Entry points

```python
run_agent(agent_graph, features_path, base_module, extra_dependencies)
build_app_container(features_path, base_module, extra_dependencies, agent_graph)
create_agent_app(container)
run_consumer_agent(container)
scan_and_load_features(features_path, base_module)
```

Full documentation: [runtime-and-container-reference.md](runtime-and-container-reference.md).

### Builders

```python
ToolAgentBuilder(llm_service, tools, system_prompt, pre_nodes, post_nodes,
                 context_budget, system_reminder, system_reminder_threshold)
AgentGraphBuilder(deps, state_schema)
FlowGraphBuilder(registry, deps, checkpointer)
```

### State

```python
AgentBaseState       # base TypedDict for all agent graphs
ToolAgentState       # extends AgentBaseState with messages + tool_results
```

### HITL

```python
interrupt(value)     # pause graph execution
HitlInterruptPayload, InterruptType, HitlResumeCommand
```

### Use cases

```python
ExecuteAgentInput, ExecuteAgentOutput
ResumeAgentInput, ResumeAgentOutput, ResumeAgentUseCase
```

### LLM

```python
make_openai_service() -> OpenAIService
OpenAIService(api_key, model, temperature)
ILLMService          # protocol
```

### Tools and remote agents

```python
tool                        # @tool decorator (re-export from langchain_core)
RemoteAgentTool             # call another registered agent as a tool
AgentDiscoveryService(registry, exclude_agent_types, tool_factory)
default_tool_factory(capability, registry)
AgentCallCoordinator(endpoint_resolver, resume_use_case, http_client,
                     capabilities, max_retries, retry_backoff_seconds)
AgentCapability(agent_type, name, description, input_schema, output_schema)
IAgentEndpointResolver      # protocol: resolve_endpoint(agent_id) -> URL
```

### Context budget

```python
ContextBudgetConfig(max_total_tokens, max_message_history, max_tool_result_tokens,
                    compaction_strategy, compaction_threshold, aggressive_threshold,
                    danger_threshold, preserve_business_payloads)
ContextBudgetManager(config, llm_service)
CompactionStrategy.TOOL_RESULT_CLEAR | SELECTIVE | HEAD_TAIL | TIERED | NONE
PayloadType.BUSINESS_DATA | NARRATIVE | ROUTING
```

### LangGraph re-exports

```python
END, ToolNode, tools_condition, StateGraph
```

### Workflow events

```python
IWorkflowEventEmitter       # protocol for custom emitter injection
WorkflowEventEmitter        # default emitter backed by IMessagePublisher
serialize_event(event)      # WorkflowEvent -> dict
workflow_event_scope(emitter, *, request, mode)
emit_workflow_event(event, *, state, node_id, topic)
```

### Settings

```python
Settings, settings          # pydantic-settings config object
```

---

## Configuration Summary {#configuration}

Full reference: [configuration.md](configuration.md).

Copy `env.example` to `.env` and set at minimum:

```bash
OPENAI_API_KEY=sk-...
APP_MODE=SERVER
```

### Key variables

| Variable | Default | Description |
|---|---|---|
| `APP_MODE` | `SERVER` | `SERVER` (FastAPI) or `CONSUMER` (Kafka/Event Mesh) |
| `SERVER_PORT` | `36000` | FastAPI listen port |
| `ALLOW_ORIGINS` | `["*"]` | CORS allowed origins |
| `OPENAI_API_KEY` | — | OpenAI credential when using the OpenAI integration |
| `LLM_MODEL` | `gpt-4o-mini` | Chat model identifier |
| `INFRA_MODE` | `mock` | `mock` → `MemorySaver`; any other value + HANA → `HanaCheckpointSaver` |
| `MESSAGING_MODE` | `""` | `mock` / `local` / `sap` — overrides `INFRA_MODE` for messaging |
| `PUSH_GATEWAY_TRANSPORT` | `grpc` | Push-gateway transport selector: `grpc` (default) or `http` |
| `PUSH_GATEWAY_URL` | `""` | HTTP push-gateway base URL used when `PUSH_GATEWAY_TRANSPORT=http` |
| `PUSH_GATEWAY_GRPC_TARGET` | `""` | Unary gRPC target used when `PUSH_GATEWAY_TRANSPORT=grpc` |
| `KAFKA_REQUEST_TOPIC` | `agent.request` | Request topic for CONSUMER mode |
| `GET_FROM_VCAP` | `False` | Load HANA credentials from `VCAP_SERVICES` |
| `REQUIRED_PARAMETERS` | `[]` | Sub-agent required input parameters |
| `NEGATIVE_EXAMPLES` | `[]` | "Do NOT call when…" phrases for LLM routing |

---

## Architecture and Module Map {#architecture-and-module-map}

Full diagram and module rules: [clean-architecture-tutorial.md](../03-building-agents/clean-architecture-tutorial.md).

### Layer summary

| Layer | Package path | Responsibility |
|---|---|---|
| L1 Domain | `agent_sdk/layer1_domain/` | State, value objects, exceptions — no external deps |
| L2 Application | `agent_sdk/layer2_application/` | Use cases, nodes, services, interface protocols |
| L3 Adapters | `agent_sdk/layer3_adapters/` | HTTP routes, Kafka consumer, serialisers |
| L4 Frameworks | `agent_sdk/layer4_frameworks/` | Graph builders, OpenAI, HANA, Kafka, httpx |

### Dependency rules

| Layer | May import from | May NOT import from |
|---|---|---|
| L1 | stdlib only | L2, L3, L4; all external packages |
| L2 | L1, own interfaces | L3, L4; framework packages |
| L3 | L1, L2 | L4 implementations directly |
| L4 | L1, L2, L3 | — |

`bootstrap.py` and `main.py` are composition-root modules permitted to import from all layers.

### DI container default keys

| Key | Default value |
|---|---|
| `logger` | `StandardLogger` |
| `monitor` | `PrometheusMonitor` |
| `agent_registry` | `HttpAgentRegistry` |
| `openai_service` | `OpenAIService` (if `OPENAI_API_KEY` set) |
| `llm` | `ChatOpenAI` (if `OPENAI_API_KEY` set) |
| `publisher` | `ConsoleMessagePublisher` / `KafkaMessagePublisher` / `EventMeshMessagePublisher` |
| `consumer` | `MockMessageConsumer` / `KafkaMessageConsumer` / `EventMeshMessageConsumer` |
| `push_gateway_notifier` | `ConsolePushGatewayNotifier` (mock) / `GrpcPushGatewayNotifier` (`PUSH_GATEWAY_TRANSPORT=grpc`) / `HttpPushGatewayNotifier` (`PUSH_GATEWAY_TRANSPORT=http`) / no-op |
| `workflow_event_emitter` | `WorkflowEventEmitter(publisher, logger, push_gateway_notifier)` |
| `execute_agent` | `ExecuteAgentUseCase` |
| `resume_agent` | `ResumeAgentUseCase` (if `agent_graph` provided) |

---

## Testing and Troubleshooting {#testing-and-troubleshooting}

Full guide: [testing-and-troubleshooting.md](testing-and-troubleshooting.md).

### Run tests

```bash
pip install -e ".[dev]"
pytest
pytest --cov=agent_sdk --cov-report=term-missing
```

### Deploy locally

```bash
python3 main.py
```

### Deploy with Docker

```bash
docker build -f deployment/Dockerfile -t agent-sdk .
docker run -p 36000:36000 -e OPENAI_API_KEY=sk-... agent-sdk
```

### Deploy on SAP BTP (Cloud Foundry)

Bind a HANA Cloud service instance and set:

```bash
GET_FROM_VCAP=true
INFRA_MODE=production
```

---

[← Docs home](../README.md) | [SDK root](../../README.md)

# Agent SDK

A framework for building AI workflow agents with LangGraph, FastAPI, and SAP BTP integration.

The SDK abstracts LangGraph, LangChain, and infrastructure concerns so that agent authors only write task-specific code: define your tools or graph nodes, wire the container, and the SDK handles HTTP serving, Kafka/Event Mesh consumption, checkpointing, registry heartbeating, and dependency injection.

Queue delivery is now the native orchestration path. Teams can adopt queue-first execution, FE event contracts, shared-state persistence, and queue-native remote delegation without reading base-agent internals; the legacy HTTP sub-agent path remains available as a compatibility adapter.

---

## Documentation

Full documentation lives under [`docs/`](docs/README.md).

| Section | What you will find |
|---|---|
| [01 — Overview](docs/01-overview/README.md) | What the SDK is, the four-layer architecture, and the capability matrix |
| [02 — Quick Start](docs/02-quickstart/README.md) | Install, run your first agent in 5 minutes, call the API |
| [03 — Building Agents](docs/03-building-agents/README.md) | All three builder paths, graph patterns, subgraphs, HITL, clean-architecture tutorial |
| [04 — Features](docs/04-features/README.md) | Checkpointing, transports, remote agents, context budget, workflow events, DI cookbook |
| [05 — Reference](docs/05-reference/README.md) | Full API surface, configuration variables, architecture details |
| [06 — Onboarding](docs/06-onboarding/README.md) | New-developer orientation: repo layout, concepts, first PR walkthrough — **[presentation overview →](docs/06-onboarding/agent-sdk-for-new-developers.md)** |

### Queue-first adoption map

- FE metadata + UI event contract: [Features → Workflow Events](docs/04-features/workflow-events.md)
- Kafka / Event Mesh ack, retry, dead-letter, and delivery rules: [Features → Transports](docs/04-features/transports.md)
- Queue-native remote delegation with legacy HTTP compatibility: [Features → Remote Agents](docs/04-features/remote-agents.md)
- Shared-state, `StateResolver`, and DI wiring: [Building Agents → DI Cookbook](docs/03-building-agents/dependency-injection-cookbook.md)
- Registry/runtime/config variables such as `AGENT_KIND`, `IS_PUBLISHED`, and queue metadata: [Reference → Configuration](docs/05-reference/configuration.md)

---

## Requirements

- Python ≥ 3.12
- Provider credentials for the model you choose (for OpenAI, set `OPENAI_API_KEY`; other providers use the env vars expected by their LangChain integration)

```bash
pip install -e ".[dev]"
```

Optional push-gateway fan-out defaults to gRPC and uses `PUSH_GATEWAY_GRPC_TARGET=host:port`. Set `PUSH_GATEWAY_TRANSPORT=http` only if you want the HTTP notifier path.

---

## 30-second Quick Start

```python
from agent_sdk import ToolAgentBuilder, make_openai_service, run_agent, tool

@tool
def wait_task(wait_type: str, duration_seconds: int = 60) -> dict:
    """Execute a wait operation."""
    return {"status": "COMPLETED", "wait_type": wait_type, "actual_duration_seconds": duration_seconds}

def main() -> None:
    builder = ToolAgentBuilder(
        llm_service=make_openai_service(
            model_kwargs={"reasoning_effort": "medium"}
        ),
        tools=[wait_task],
        system_prompt="You are a workflow assistant.",
    )
    run_agent(agent_graph=builder.compile())

if __name__ == "__main__":
    main()
```

```bash
OPENAI_API_KEY=sk-... \
LLM_PROVIDER=openai \
LLM_MODEL=gpt-4o-mini \
LLM_MODEL_KWARGS='{"reasoning_effort": "medium"}' \
python examples/minimal_tool_agent.py
```

`LLM_*` settings are provider-agnostic SDK inputs. The SDK forwards `LLM_PROVIDER`, `LLM_TEMPERATURE`, `LLM_TIMEOUT`, `LLM_MAX_TOKENS`, `LLM_MAX_RETRIES`, and any JSON object supplied in `LLM_MODEL_KWARGS` to LangChain unchanged, except when a `LLM_MODEL_KWARGS` key duplicates one of those first-class SDK options.

For standard OpenAI chat models, prefer `reasoning_effort="medium"` (or `{"reasoning_effort": "medium"}` in `LLM_MODEL_KWARGS`). For example, use `model_kwargs={"reasoning_effort": "medium"}`.

Responses API / Azure OpenAI style kwargs use `{"reasoning": {"effort": "medium"}}` instead. For example, use `model_kwargs={"reasoning": {"effort": "medium"}}` when you intentionally opt into that path.

Full walkthrough: [02 — Quick Start](docs/02-quickstart/README.md)

---

## Registry Interop

When `REGISTRY_URL` is set, the SDK talks to the registry's current `/api/v1` contract directly. Agent startup reconciles `name = AGENT_TYPE`, writes `version = AGENT_VERSION`, advertises `/api/v1/execute` plus `/health`, then publishes and activates the agent through the registry lifecycle endpoints.

Local LangChain `@tool` tools supplied directly to the SDK are auto-registered as inline tools under deterministic qualified names such as `invoice-agent__validate_invoice__1.2.0`. Repeat startups reuse the existing tool ID for the same qualified name instead of creating duplicates. MCP-discovered tools and remote-agent tools are not auto-registered in this flow.

Supervisor discovery and HTTP compatibility resolution accept the registry's current response shape: `id`, `name`, `invoke_endpoint`, `healthcheck_endpoint`, and optional `metadata`. The SDK reads routing and queue metadata when the registry returns it, but does not claim full metadata write round-trip because the current registry write API still drops richer metadata fields.

---

## Choose Your Path

| I want to… | Go to |
|---|---|
| Run a working agent in 5 minutes | [Quick Start](docs/02-quickstart/README.md) |
| Understand the SDK and its architecture | [Overview](docs/01-overview/README.md) |
| Build a custom graph, add HITL, or use subgraphs | [Building Agents](docs/03-building-agents/README.md) |
| Add checkpointing, Kafka, or remote agents | [Features](docs/04-features/README.md) |
| Look up a class, function, or env var | [Reference](docs/05-reference/README.md) |
| Get oriented as a new developer | **[Agent SDK for New Developers](docs/06-onboarding/agent-sdk-for-new-developers.md)** — recommended new-team-member entry point |

---

## Web Documentation Site

A rendered web version of this documentation is available via the standalone docs site at `../agent-sdk-docs-site/`.

To run it locally:

```bash
cd ../agent-sdk-docs-site
npm install
npm run sync-docs   # regenerate assets from this folder
npm run dev         # open http://localhost:5173
```

**Canonical-source rule:** all documentation is authored here (`agent-sdk/README.md` and `agent-sdk/docs/`). After editing, regenerate the site assets with `npm run sync-docs`. Never hand-author content inside `agent-sdk-docs-site/`.

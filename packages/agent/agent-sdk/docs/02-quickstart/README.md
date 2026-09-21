# 02 — Quick Start

[← Docs home](../README.md) | [SDK root](../../README.md)

---

## Pages in this section

| Page | What you get |
|---|---|
| [First Agent](first-agent.md) | Step-by-step: write a tool, build, run, and call the agent |
| [SDK Mental Model](mental-model.md) | What an agent is, what the SDK handles, vocabulary |
| [Choose Your Builder](choose-your-builder.md) | Path A / B / C selection guide with consistent naming |
| [Examples Roadmap](examples-roadmap.md) | All examples grouped by learning stage |

---

## Prerequisites

- Python ≥ 3.12
- An OpenAI API key (`OPENAI_API_KEY`)

---

## 1. Install

```bash
git clone <repo-url>
cd apps/backend/agent/agent-sdk
pip install -e ".[dev]"
```

Copy the example environment file:

```bash
cp env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...
```

Minimum `.env` content:

```bash
OPENAI_API_KEY=sk-...
APP_MODE=SERVER
```

---

## 2. Run Your First Agent

```bash
OPENAI_API_KEY=sk-... python3 examples/minimal_tool_agent.py
```

The agent starts on `http://0.0.0.0:36000`. You will see:

```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:36000
```

---

## 3. Call the Agent

```bash
curl -X POST http://localhost:36000/api/v1/execute \
     -H 'Content-Type: application/json' \
     -d '{"message": "wait for 10 seconds", "conv_id": "conv-1"}'
```

Successful response:

```json
{
  "status": "success",
  "message": "Waiting 10 seconds (fixed_duration).",
  "session_id": "9f1a2b3c-4d5e-6f7a-8b9c-0d1e2f3a4b5c",
  "interrupted": false,
  "interrupt_payload": null,
  "agent_data": {},
  "error": null,
  "error_code": null,
  "correlation_id": null,
  "duration_ms": 528.1
}
```

---

## 4. What the Minimal Example Does

```python
from agent_sdk import ToolAgentBuilder, make_openai_service, run_agent, tool

@tool
def wait_task(wait_type: str, duration_seconds: int = 60) -> dict:
    """Execute a wait operation.

    Args:
        wait_type: The type of wait (e.g. 'fixed_duration').
        duration_seconds: How long to wait in seconds.
    """
    return {"status": "COMPLETED", "wait_type": wait_type, "actual_duration_seconds": duration_seconds}


def main() -> None:
    builder = ToolAgentBuilder(
        llm_service=make_openai_service(),
        tools=[wait_task],
        system_prompt="You are a workflow assistant. Use available tools to help the user.",
    )
    run_agent(agent_graph=builder.compile())


if __name__ == "__main__":
    main()
```

- `@tool` turns a regular function into a LangChain tool with schema inference.
- `ToolAgentBuilder` creates a standard tool-calling graph — LLM decides which tool to call, tool executes, LLM formats the response.
- `run_agent` starts FastAPI + uvicorn and wires the DI container.

---

## 5. Available Endpoints (SERVER mode)

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check — `{"status": "ok"}` |
| `GET` | `/ready` | Readiness check — `{"status": "ready"}` |
| `GET` | `/api/v1/info` | Agent metadata |
| `POST` | `/api/v1/execute` | Run the agent |
| `POST` | `/api/v1/resume` | Resume after HITL interrupt |

---

## 6. Example File Index

For a learning-stage breakdown of all examples, see [Examples Roadmap](examples-roadmap.md).

| File | What it demonstrates |
|---|---|
| `examples/minimal_tool_agent.py` | Path A — `ToolAgentBuilder` with one tool — **start here** |
| `examples/tool_agent_example.py` | Path A — `ToolAgentBuilder` with multiple tools |
| `examples/convenient_tool_agent.py` | Path B — `AgentGraphBuilder` with manual node wiring |
| `examples/hitl_agent_example.py` | `interrupt()` and `/api/v1/resume` |
| `examples/subgraph_example.py` | `AgentGraphBuilder.add_subgraph()` |
| `examples/echo_agent/` | Minimal multi-file agent project |
| `examples/flow_graph_example.py` | Path C — `FlowGraphBuilder` with `NodeTypeRegistry` |
| `examples/remote_agent_example.py` | `RemoteAgentTool` and `AGENT_CALL` interrupt |
| `examples/queue_native_supervisor_example.py` | queue-first supervisor example with `AsyncAgentDelegator`, `MessageReactionRouter`, and registry queue routing |
| `examples/supervisor_agent_example.py` | HTTP compatibility supervisor with `AgentCallCoordinator` and `AgentDiscoveryService` |
| `examples/context_budget_example.py` | `ContextBudgetManager` compaction strategies |
| `examples/kafka_consumer_example.py` | SAP Event Mesh-native consumer path with Kafka local compatibility; execute / delegation / correlated resume pipeline |

Annotated walkthroughs: [examples.md](../examples.md)

---

## Next Steps

- **Step-by-step first agent** → [First Agent](first-agent.md)
- **Understand the SDK** → [SDK Mental Model](mental-model.md)
- **Choose a builder path** → [Choose Your Builder](choose-your-builder.md) and [03 — Building Agents](../03-building-agents/README.md)
- **Full env var reference** → [Reference → Configuration](../05-reference/configuration.md)
- **Understand the architecture** → [01 — Overview](../01-overview/README.md)

---

[← Docs home](../README.md) | [SDK root](../../README.md)

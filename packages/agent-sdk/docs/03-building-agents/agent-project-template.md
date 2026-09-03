# Agent Project Template

[← Layer-by-Layer Guide](layer-by-layer-guide.md) | [← Building Agents](README.md)

---

Copy this template to bootstrap a new agent that follows the SDK's clean-architecture pattern.

---

## Folder structure

```
my-agent/
├── app/
│   ├── __init__.py
│   ├── layer1_domain/
│   │   ├── __init__.py
│   │   └── state.py              # AgentBaseState subclass — data only
│   ├── layer2_application/
│   │   ├── __init__.py
│   │   ├── nodes.py              # Graph node functions — (state, deps) → partial update
│   │   └── ports.py              # Protocol interfaces for external services (optional)
│   ├── layer3_adapters/          # Usually empty — the SDK provides standard adapters
│   │   └── __init__.py
│   └── layer4_frameworks/
│       ├── __init__.py
│       ├── config/
│       │   ├── __init__.py
│       │   └── app_config.py     # Pydantic settings
│       └── graph/
│           ├── __init__.py
│           └── my_graph.py       # build_graph(deps) — wires nodes with AgentGraphBuilder
├── bootstrap.py                  # Composition root — builds the DI container
├── main.py                       # Entry point — calls create_agent_app(container)
├── pyproject.toml
└── .env
```

---

## File templates

### `app/layer1_domain/state.py`

```python
from agent_sdk import AgentBaseState


class MyAgentState(AgentBaseState, total=False):
    # Add your agent-specific state fields here.
    # AgentBaseState already provides: message, session_id, conv_id,
    # user_id, tenant_id, parameters, formatted_response, error, error_code, etc.
    my_field: str
    my_result: dict
```

---

### `app/layer2_application/nodes.py`

```python
from app.layer1_domain.state import MyAgentState


def my_first_node(state: MyAgentState, deps: dict) -> dict:
    logger = deps["logger"]
    value = state.get("parameters", {}).get("my_param", "default")
    logger.info("Processing", value=value)
    return {"my_field": value}


async def my_second_node(state: MyAgentState, deps: dict) -> dict:
    my_service = deps["my_service"]
    result = await my_service.process(state.get("my_field", ""))
    return {"my_result": result, "agent_result": {"content": str(result), "status": "ok"}}
```

**Rules:**
- Return only the keys you update (partial state update).
- Access external services via `deps["key"]` — never instantiate them here.
- No imports from LangGraph, LangChain, httpx, pydantic, or openai.

---

### `app/layer2_application/ports.py` (optional — only needed for sub-agent calls or custom services)

```python
from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class MyServicePort(Protocol):
    async def process(self, value: str) -> Dict[str, Any]: ...
```

---

### `app/layer4_frameworks/config/app_config.py`

```python
from pydantic_settings import BaseSettings


class AppSettings(BaseSettings):
    SERVER_PORT: int = 8080
    MY_SERVICE_URL: str = "http://localhost:9000"

    class Config:
        env_file = ".env"


settings = AppSettings()
```

---

### `app/layer4_frameworks/graph/my_graph.py`

```python
from typing import Any, Dict, Optional

from agent_sdk import AgentGraphBuilder

from app.layer1_domain.state import MyAgentState
from app.layer2_application.nodes import my_first_node, my_second_node


def build_graph(deps: Optional[Dict[str, Any]] = None):
    deps = deps or {}

    builder = AgentGraphBuilder(deps=deps, state_schema=MyAgentState)
    builder.add_node("first", my_first_node)
    builder.add_node("second", my_second_node)
    builder.set_entry_point("first")
    builder.add_edge("first", "second")
    return builder.compile()
```

---

### `bootstrap.py`

```python
from agent_sdk import build_app_container as _sdk_build_app_container

from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.graph.my_graph import build_graph


class _MyService:
    async def process(self, value: str) -> dict:
        return {"processed": value.upper()}


def build_app_container() -> dict:
    my_service = _MyService()

    graph = build_graph(deps={"my_service": my_service})

    return _sdk_build_app_container(
        agent_graph=graph,
        extra_dependencies={
            "settings": settings,
            "my_service": my_service,
        },
    )
```

**Key points:**
- `bootstrap.py` is the **only** place services are instantiated.
- Pass shared objects both to the graph factory (`deps=`) and to `extra_dependencies` so they are available to both nodes and use cases.
- Never import from `bootstrap.py` inside the `app/` package.

---

### `main.py`

```python
import uvicorn

from bootstrap import build_app_container
from app.layer4_frameworks.config.app_config import settings


def create_app():
    from agent_sdk import create_agent_app

    container = build_app_container()
    return create_agent_app(container)


app = create_app()

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.SERVER_PORT, reload=True)
```

---

## Adding conditional edges

```python
# app/layer2_application/nodes.py
def route_node(state: dict) -> str:
    if state.get("my_result", {}).get("needs_retry"):
        return "first"
    return "__end__"

# app/layer4_frameworks/graph/my_graph.py
builder.add_conditional_edges(
    "second",
    route_node,
    {"first": "first", "__end__": "__end__"},
)
```

---

## Adding HITL

```python
# app/layer2_application/nodes.py
from agent_sdk import interrupt, InterruptType, HitlInterruptPayload

async def approval_node(state: dict, deps: dict) -> dict:
    interrupt(HitlInterruptPayload(
        interrupt_type=InterruptType.GENERIC,
        data={"message": "Approve this action?", "payload": state.get("my_result")},
    ))
    return {}

# app/layer4_frameworks/graph/my_graph.py
from langgraph.checkpoint.memory import MemorySaver

def build_graph(deps=None):
    deps = deps or {}
    checkpointer = deps.get("checkpointer") or MemorySaver()
    builder = AgentGraphBuilder(deps=deps, state_schema=MyAgentState)
    builder.add_node("first", my_first_node)
    builder.add_node("approval", approval_node)
    builder.add_node("second", my_second_node)
    builder.set_entry_point("first")
    builder.add_edge("first", "approval")
    builder.add_edge("approval", "second")
    return builder.compile(checkpointer=checkpointer)
```

Full HITL guide: [Features → HITL](../04-features/hitl.md)

---

## Adding input/output guards

```python
# bootstrap.py — add guard to extra_dependencies
from agent_sdk import input_guard_node, output_guard_node

class MyInputGuard:
    def validate(self, message: str, **context) -> dict:
        if not message.strip():
            return {"passed": False, "rejection_reason": "Empty message"}
        return {"passed": True, "sanitized_message": message.strip()}

# app/layer4_frameworks/graph/my_graph.py — add nodes
builder.add_node("input_guard", input_guard_node)
builder.set_entry_point("input_guard")
builder.add_edge("input_guard", "first")
```

Full guard guide: [Building Agents → Architecture Rules](architecture-rules-and-anti-patterns.md)

---

## Common `extra_dependencies` keys

| Key | Type | Auto-injected? | Notes |
|---|---|---|---|
| `logger` | `ILogger` | Yes (StandardLogger) | Override with your own |
| `monitor` | `IMonitor` | Yes (PrometheusMonitor) | Override with your own |
| `agent_registry` | `IAgentRegistry` | Yes (HttpAgentRegistry) | |
| `openai_service` | `OpenAIService` | Yes if `OPENAI_API_KEY` set | |
| `llm` | `ChatOpenAI` | Yes if `OPENAI_API_KEY` set | |
| `publisher` | `IMessagePublisher` | Yes (from `MESSAGING_MODE`) | |
| `consumer` | `IMessageConsumer` | Yes if `APP_MODE != SERVER` | |
| `settings` | your settings class | No — pass explicitly | |
| `checkpointer` | checkpointer | No — pass explicitly for HITL | |

---

[← Layer-by-Layer Guide](layer-by-layer-guide.md) | [Rules & Anti-Patterns →](architecture-rules-and-anti-patterns.md) | [← Building Agents](README.md)

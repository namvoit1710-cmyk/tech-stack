# Architecture Rules and Anti-Patterns

[← Agent Project Template](agent-project-template.md) | [← Building Agents](README.md)

---

This page is a quick-reference checklist for code review and self-review. Every rule is drawn from the SDK architecture or a pattern found in the public SDK examples.

---

## The five mandatory rules

These are enforced by the architecture boundary test suite (`tests/unit/test_architecture_boundaries.py`).

| # | Rule | Where it is tested |
|---|---|---|
| 1 | Layer 1 imports only stdlib and `agent_sdk.layer1_domain` | `TestLayer1OnlyImportsStdlib` |
| 2 | Layer 2 does not import `layer3_adapters`, `layer4_frameworks`, or any framework package | `TestLayer2NoFrameworkLeakage` |
| 3 | Layer 3 does not import `layer4_frameworks` | `TestLayer3NoLayer4Imports` |
| 4 | `bootstrap.py` is the only file that assembles the DI container | Convention (no test) |
| 5 | `main.py` contains no business logic | Convention (no test) |

The queue-first upgrade adds more SDK-owned contracts, but it does **not** relax these rules. `ConversationMetadata`, FE/UI event envelopes, orchestration envelopes, shared-state records, and registry/runtime contracts stay in Layer 1 because they are plain stdlib dataclasses / `TypedDict`s. Queue message routing helpers such as `StateResolver`, `ensure_definition`, snapshot utilities, and message-reaction services stay in Layer 2 because they depend only on Layer 1 contracts and `Protocol` ports, not on broker SDKs.

Run the boundary tests with:

```bash
python3 -m pytest tests/unit/test_architecture_boundaries.py -q
```

---

## Anti-patterns by layer

### Layer 1 anti-patterns

**❌ Importing a framework package in state.py**

```python
# BAD
from pydantic import BaseModel

class MyState(BaseModel):  # pydantic is a framework — not allowed in Layer 1
    message: str
```

```python
# GOOD
from agent_sdk import AgentBaseState

class MyState(AgentBaseState, total=False):
    message: str
```

State must extend `AgentBaseState` (a `TypedDict`). Use `total=False` to make all fields optional, which is required for partial state updates.

---

**❌ Putting business logic in state.py**

```python
# BAD
class MyState(AgentBaseState, total=False):
    def validate(self) -> bool:   # Methods belong in Layer 2
        return bool(self.get("message"))
```

State is a data container. Validation logic belongs in a Layer 2 node or helper function.

---

### Layer 2 anti-patterns

**Queue-first package rule:** `layer2_application/utils/` is the home for framework-free helper objects such as `StateResolver`, dependency resolvers, and shared-state snapshot functions. These helpers are allowed in Layer 2 because they express application logic over Layer 1 types only; do not move them into Layer 4 just because queue-based agents use them.

**❌ Importing LangGraph inside a node**

```python
# BAD
from langgraph.graph import StateGraph

async def my_node(state: dict, deps: dict) -> dict:
    g = StateGraph(...)   # Layer 4 framework leak into Layer 2
```

```python
# GOOD
async def my_node(state: dict, deps: dict) -> dict:
    svc = deps["my_service"]
    result = await svc.process(state["message"])
    return {"agent_result": result}
```

If you need to call a sub-graph, inject it as a dependency and call it via a Protocol.

---

**❌ Hardcoding a service URL in a node**

```python
# BAD
async def my_node(state: dict, deps: dict) -> dict:
    import httpx
    async with httpx.AsyncClient() as client:
        r = await client.post("https://api.example.com/...", ...)
```

HTTP clients belong in Layer 4. The node should receive a service object via `deps["my_service"]` and call an abstract method on it.

The same rule applies to queue delivery. `ack()`, `nack(requeue=True)`, and `reject()` are exposed to Layer 2 through `IMessageDelivery`, but Kafka commit APIs, Event Mesh settlement APIs, and broker SDK clients remain in Layer 4.

---

**❌ Full state replacement**

```python
# BAD
async def my_node(state: dict, deps: dict) -> dict:
    return {**state, "agent_result": "done"}  # Copies all keys — fragile
```

```python
# GOOD
async def my_node(state: dict, deps: dict) -> dict:
    return {"agent_result": "done"}  # Return only what changed
```

LangGraph merges the returned dict into state. Returning the full state dict creates unnecessary coupling and can overwrite fields set by other nodes.

---

**❌ Directly importing a Layer 4 concrete class as a type hint**

```python
# BAD
from app.layer4_frameworks.delegates.remote_agent_delegate import RemoteAgentDelegate

async def my_node(state: dict, deps: dict) -> dict:
    delegate: RemoteAgentDelegate = deps["delegate"]  # Layer 4 import in Layer 2
```

```python
# GOOD — declare a Protocol in Layer 2
from app.layer2_application.ports import SubAgentDelegate

async def my_node(state: dict, deps: dict) -> dict:
    delegate: SubAgentDelegate = deps["delegate"]  # Protocol — no framework import
```

---

### Layer 3 anti-patterns

**❌ Business logic in a route handler**

```python
# BAD
@router.post("/execute")
async def execute(payload: MyInput):
    # inline business logic
    if not payload.message:
        return {"error": "empty"}
    result = some_function(payload.message)
    return {"result": result}
```

```python
# GOOD — delegate to Layer 2 use case
@router.post("/execute", response_model=ExecuteAgentOutputPydantic)
async def execute(payload: ExecuteAgentInputPydantic):
    domain_input = payload.to_dataclass(ExecuteAgentInput)
    domain_output = await exec_uc.execute(domain_input)
    return ExecuteAgentOutputPydantic.from_dataclass(domain_output)
```

Route handlers translate between transport (JSON/HTTP) and domain objects. They must not implement business logic.

This includes queue presenters. `run_consumer_agent()` may translate raw broker payloads into `ExecuteAgentInput`, `ResumeAgentInput`, FE metadata, and orchestration envelopes, then hand off to Layer 2 routers/use cases. It must not embed retry policy, planner logic, or broker-specific business branching.

---

**❌ Importing Layer 4 from Layer 3**

```python
# BAD
from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder
```

Layer 3 adapters must not depend on Layer 4 implementations. If you need a builder, it belongs in Layer 4 or `bootstrap.py`.

---

### Layer 4 anti-patterns

**❌ Creating a service inside the graph factory**

```python
# BAD
def build_graph():
    from openai import OpenAI
    client = OpenAI()           # Hardcoded — can't be swapped in tests
    builder = AgentGraphBuilder(deps={"client": client}, ...)
```

```python
# GOOD — accept deps as an argument
def build_graph(deps=None):
    deps = deps or {}
    builder = AgentGraphBuilder(deps=deps, ...)
```

The graph factory must accept dependencies from outside. `bootstrap.py` is responsible for creating services; the graph factory only wires them.

---

**❌ Reading environment variables inside a node**

```python
# BAD
import os

def my_node(state: dict, deps: dict) -> dict:
    url = os.environ["MY_SERVICE_URL"]   # Config access in Layer 2 node
```

```python
# GOOD — read config in bootstrap.py, inject into deps
# bootstrap.py
settings = AppSettings()
graph = build_graph(deps={"service_url": settings.MY_SERVICE_URL})

# node
def my_node(state: dict, deps: dict) -> dict:
    url = deps["service_url"]
```

Configuration belongs in `app/layer4_frameworks/config/`. Nodes receive resolved values through `deps`.

---

### `bootstrap.py` anti-patterns

**❌ Business logic in bootstrap.py**

```python
# BAD
def build_app_container():
    if os.getenv("FEATURE_X"):
        do_something_complicated()  # Logic belongs in a use case
    ...
```

`bootstrap.py` wires objects together. It must not implement business rules or perform data transformations.

---

**❌ Creating the container inside `main.py` instead of `bootstrap.py`**

```python
# BAD (in main.py)
from agent_sdk import build_app_container, AgentGraphBuilder
from app.layer2_application.nodes import my_node

builder = AgentGraphBuilder(...)
graph = builder.compile()
container = build_app_container(agent_graph=graph)
```

Assembly logic belongs in `bootstrap.py`. `main.py` calls `build_app_container()` and nothing else.

---

## Quick-reference checklist

Use this before every pull request:

**Layer 1**
- [ ] `state.py` extends `AgentBaseState` with `total=False`
- [ ] No third-party imports in `app/layer1_domain/`
- [ ] No methods or business logic in state classes
- [ ] Queue-first contracts (`ConversationMetadata`, shared-state records, queue metadata, execution policy) stay as stdlib-only data models

**Layer 2**
- [ ] All nodes have signature `(state: dict, deps: dict) -> dict`
- [ ] Nodes return partial state dicts (only changed keys)
- [ ] No `langgraph`, `langchain*`, `httpx`, `pydantic`, `openai` imports in `app/layer2_application/`
- [ ] External service interfaces are `Protocol` classes in `ports.py`
- [ ] `layer2_application/utils/` contains only framework-free helpers such as `StateResolver` and shared-state snapshot utilities
- [ ] Queue handlers depend on `IMessageDelivery` / `IAgentDelegator`, never Kafka or Event Mesh clients directly

**Layer 3** (if present)
- [ ] Route handlers call Layer 2 use cases — no inline logic
- [ ] DTOs have `.to_dataclass()` and `.from_dataclass()` methods
- [ ] No `layer4_frameworks` imports

**Layer 4**
- [ ] Graph factory accepts `deps` as an argument
- [ ] Pydantic settings in `app/layer4_frameworks/config/`
- [ ] Concrete Protocol implementations only here or in `bootstrap.py`
- [ ] Kafka/Event Mesh ack + retry implementations and HANA shared-state persistence stay here

**bootstrap.py**
- [ ] Only place services are instantiated
- [ ] Calls `build_app_container(agent_graph=..., extra_dependencies=...)`
- [ ] No business logic

**main.py**
- [ ] Calls `build_app_container()` and `create_agent_app(container)`
- [ ] Nothing else

---

## Running all architecture checks

```bash
# Layer boundary tests
python3 -m pytest tests/unit/test_architecture_boundaries.py -q

# Bootstrap / DI container tests
python3 -m pytest tests/test_bootstrap.py -q

# Verify tutorial files cover all layers
rg -n "Layer 1|Layer 2|Layer 3|Layer 4|bootstrap.py|main.py" \
    docs/03-building-agents/clean-architecture-tutorial.md \
    docs/03-building-agents/layer-by-layer-guide.md \
    docs/03-building-agents/agent-project-template.md \
    docs/03-building-agents/architecture-rules-and-anti-patterns.md
```

---

[← Agent Project Template](agent-project-template.md) | [← Building Agents](README.md)

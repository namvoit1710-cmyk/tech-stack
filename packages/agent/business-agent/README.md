# Base Agent Blueprint

Reusable Python agent scaffold built on `agent-sdk`. It provides a minimal layered service with one compiled graph, a dependency container, stubbed adapters, and regression tests that lock the current scaffold behavior.

## Blueprint Maturity

This blueprint is intentionally incomplete.

- App bootstrap, graph compilation, dependency assembly, health/readiness endpoints, and tool discovery are implemented.
- Business logic is still starter logic. The queue handler is a stub, the workflow client can fall back to mock payloads, and the use cases currently produce sample plan and review data.
- The codebase enforces architectural boundaries with tests, but it still contains a small number of pragmatic scaffold shortcuts that are worth tightening when you build a real service.

Use this repo as a starting point for a new agent service, not as a finished business workflow.

## Current Structure

```text
base-agent/
├── app/
│   ├── layer1_domain/
│   │   ├── constants/
│   │   └── entities/
│   ├── layer2_application/
│   │   ├── enums/
│   │   ├── features/
│   │   │   └── main_agent/
│   │   │       └── usecases/
│   │   ├── graphs/
│   │   │   ├── edges/
│   │   │   ├── nodes/
│   │   │   └── main_flow.py
│   │   ├── interfaces/
│   │   ├── prompts/
│   │   ├── queues/
│   │   └── tools/
│   └── layer4_frameworks/
│       ├── clients/
│       ├── config/
│       ├── dependency/
│       ├── graph/
│       ├── repositories/
│       └── tools/
├── tests/
│   ├── integration/
│   └── unit/
├── bootstrap.py
├── main.py
├── env.example
└── requirements.txt
```

## Current Runtime Architecture

### 1. Composition Root

- `bootstrap.py` is the only composition root.
- It creates the checkpointer, builds runtime dependencies, compiles the graph, registers the queue handler, and exposes app-local tools to `agent-sdk`.
- `main.py` stays thin. It delegates container creation to `bootstrap.py`, then calls `agent_sdk.create_agent_app(...)` to build the FastAPI app.

### 2. Dependency Assembly

`app/layer4_frameworks/dependency/ioc_container.py` is the runtime dependency builder.

Current behavior:

- Always registers `settings`, `logger`, `shared_state_repository`, `workflow_client`, and `worker_tools`.
- Conditionally registers `openai_service` only when `LLM_MODEL` is configured.
- Conditionally initializes and registers `hana_db` only when `HANA_HOST` is configured.
- Loads feature classes dynamically from `features/main_agent/usecases/`.
- Instantiates use case objects by matching constructor parameter names against the dependency container.

This means new use cases become available by adding a module under `features/main_agent/usecases/` and keeping constructor parameter names aligned with the dependency keys.

### 3. Graph Registration

The current graph is split across two layers:

- `app/layer2_application/graphs/main_flow.py`: registers nodes, entry point, and transitions.
- `app/layer4_frameworks/graph/graph_builder.py`: creates the concrete graph builder with `MainState` and compiles it.

Graph node handlers live under `app/layer2_application/graphs/nodes/`. Each node resolves a use case from the use-case registry and converts state payloads into DTOs.

### 4. Tool Discovery

Tool registration is decorator-driven.

- Mark application tool functions with `app.layer2_application.interfaces.register_tool.tool`.
- `app/layer4_frameworks/tools/tool_registry.py` scans `app.layer2_application.tools` recursively.
- Every marked function is wrapped with `agent_sdk.tool` and exposed through `build_tools()`.

The current example tool is `get_current_time()` in `app/layer2_application/tools/current_time_tool.py`.

## What Goes Where

### Root Files

- `bootstrap.py`: the only runtime composition root. Keep full wiring here and keep business rules out.
- `main.py`: FastAPI entrypoint only.
- `env.example`: document every required environment variable here.
- `requirements.txt`: service-specific Python dependencies.
- `tests/`: regression coverage for the scaffold and your custom logic.

### `app/layer1_domain/`

Put pure domain definitions here.

- `constants/`: static defaults and domain-level constants.
- `entities/`: serializable domain models and typed state payloads. Current examples include `WorkflowDefinition`, `QueueDefinition`, `ExecutionPlan`, `WorkflowExecutionResult`, and `MainState`.

Keep this layer framework-agnostic.

### `app/layer2_application/`

Put orchestration logic here.

- `enums/`: application-level enums used by orchestration code. The current graph node names live here.
- `features/main_agent/usecases/`: business use cases plus the dynamic registry in `__init__.py`.
- `graphs/main_flow.py`: the main workflow registration function.
- `graphs/nodes/`: thin graph node handlers that map graph state to use-case DTOs.
- `graphs/edges/`: reusable routing helpers such as review outcome routing.
- `interfaces/`: contracts and small application-facing helpers such as logger and graph builder abstractions, plus the tool marker decorator.
- `prompts/`: prompt assets or prompt builders.
- `queues/`: queue-triggered entrypoints.
- `tools/`: application-defined tools that can be exposed to the runtime.

Current note: most framework concerns stay out of Layer 2, but the scaffold still imports the workflow client contract from `layer4_frameworks/clients/workflow_clients.py` inside the plan and execute use cases. That shortcut is tolerated by the current scaffold, but a production service should usually move that contract fully behind `interfaces/`.

### `app/layer4_frameworks/`

Put concrete runtime implementations here.

- `clients/`: outbound service clients. The current workflow client provides mock responses when no base URL is configured.
- `config/`: environment-backed settings.
- `dependency/`: runtime container assembly.
- `graph/`: concrete graph compilation.
- `repositories/`: persistence adapters.
- `tools/`: framework-side tool discovery and wrapping.

This layer should adapt application contracts to real infrastructure rather than own business workflow decisions.

## Current Runtime Flow

The current compiled graph is:

`Plan -> Execute -> Review`

After `Review`, `route_after_review(state)` checks `state["is_reviewed"]`.

- If `is_reviewed` is `True`, the graph ends.
- If `is_reviewed` is explicitly `False`, the graph loops back to `Plan`.
- If `is_reviewed` is missing, the current default is also to end the graph.

That default is part of the current tested scaffold contract.

## Current Use Case Behavior

The starter use cases are implemented only far enough to demonstrate the workflow shape.

- `PlanUseCase` fetches workflow info from the workflow client, creates a sample `ExecutionPlan`, and returns `is_reviewed=False`.
- `ExecuteUseCase` triggers the workflow client and returns a `WorkflowExecutionResult` that echoes the planned steps.
- `ReviewUseCase` creates a summary message and returns `is_reviewed=True`.
- `workflow_queue_handler.py` currently returns an empty dictionary.

Treat these as placeholders that protect wiring contracts, not finished business logic.

## How To Customize This Blueprint

1. Copy `base-agent/` into your new service folder.
2. Rename `features/main_agent/` if the starter feature name is too generic.
3. Replace the sample logic in the use cases under `features/.../usecases/`.
4. Expand `MainState` and the domain entities under `layer1_domain/entities/` when the workflow needs more state.
5. Update `graphs/main_flow.py`, graph nodes, and routing helpers when the workflow shape changes.
6. Replace the stub workflow client and repositories with real adapters.
7. Implement `queues/workflow_queue_handler.py` when the agent actually consumes queue messages.
8. Add application tools under `layer2_application/tools/` and mark them with the tool decorator when they should be exposed at runtime.
9. Add `layer3_adapters/` only if the service genuinely needs an extra adapter seam between application code and framework code.

## Folder Rules

- Keep domain entities small and serializable.
- Keep graph nodes thin and orchestration-focused.
- Keep `bootstrap.py` as the only full-runtime wiring point.
- Keep framework implementations in `layer4_frameworks/`.
- Keep placeholder behavior explicit until it is replaced.
- Tighten the Layer 2 to Layer 4 boundary as you productize the scaffold.

## Validation

Run tests from `base-agent/`.

```powershell
.\.venv\Scripts\python -m pytest tests\unit -q
.\.venv\Scripts\python -m pytest tests\integration -q
```

Current regression coverage includes:

- `tests/unit/test_architecture_boundaries.py`: layer import guardrails and optional `layer3_adapters` handling.
- `tests/unit/test_bootstrap_smoke.py`: app container creation and FastAPI app creation.
- `tests/unit/test_dependency_resolver.py`: `MainState`, workflow defaults, and queue defaults.
- `tests/unit/test_graph.py`: graph registration and review routing.
- `tests/unit/test_plan_use_case.py`: current plan use-case scaffold contract.
- `tests/unit/test_queue_handler.py`: current queue handler scaffold contract.
- `tests/unit/test_tool_registry.py`: decorator-based tool discovery.
- `tests/unit/test_workflow_client.py`: current workflow client scaffold contract.
- `tests/integration/test_api.py`: `/health` and `/ready` endpoints.

## Example API Call

```bash
curl -X POST http://localhost:36001/api/v1/execute \
	-H "Content-Type: application/json" \
	-d '{
		"message": "Plan and execute the onboarding workflow",
		"parameters": {
			"workflow": {
				"workflow_id": "wf-1001"
			}
		}
	}'
```
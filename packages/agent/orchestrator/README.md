# AI Workflow Orchestrator

This service orchestrates conversation-driven workflow execution behind a REST API. It follows a four-layer clean architecture:

- `layer1_domain` holds pure domain entities, constants, and business rules.
- `layer2_application` holds use cases, graph registration, and application contracts.
- `layer3_adapters` exposes transport adapters, currently REST controllers.
- `layer4_frameworks` implements infrastructure such as messaging, persistence, AI providers, monitoring, and graph compilation.

The structure is inspired by the base-agent scaffold, but the descriptions below reflect the current implementation in this repository.

## Current Structure

```text
orchestrator/
├── app/
│   ├── layer1_domain/
│   │   ├── constants/
│   │   ├── entities/
│   │   │   ├── classifier/
│   │   │   ├── conversation/
│   │   │   ├── data_preprocesser/
│   │   │   ├── event/
│   │   │   ├── guardrail/
│   │   │   ├── orchestrator/
│   │   │   ├── permission/
│   │   │   └── planner/
│   │   └── enums/
│   ├── layer2_application/
│   │   ├── features/
│   │   │   ├── conversation/
│   │   │   ├── orchestrate/
│   │   │   └── orchestrate_graph/
│   │   ├── graphs/
│   │   │   ├── edges/
│   │   │   ├── nodes/
│   │   │   └── orchestration_flow.py
│   │   ├── interfaces/
│   │   └── utils/
│   ├── layer3_adapters/
│   │   └── controllers/
│   └── layer4_frameworks/
│       ├── ai/
│       ├── clients/
│       ├── config/
│       ├── graph/
│       ├── logging/
│       ├── messaging/
│       ├── monitoring/
│       ├── persistence/
│       ├── repositories/
│       └── security/
├── docs/
├── plans/
├── tests/
├── bootstrap.py
├── main.py
├── pytest.ini
└── requirements.txt
```

## Current Runtime Architecture

### 1. Composition Root

- `bootstrap.py` is the runtime composition root.
- `build_app_container()` creates the pending request store, infrastructure bundle, domain services, graph dependencies, compiled orchestration graph, and the instantiated use-case container.
- It also registers message handlers for orchestrator responses and workflow events before returning the container.

### 2. Application Entrypoint

- `main.py` stays thin and focuses on HTTP application setup.
- `create_app()` builds the FastAPI application, registers CORS middleware, exposes `/health`, and mounts the orchestrate and conversation routers.
- `get_app_container()` memoizes the container with `lru_cache`, and the FastAPI lifespan hook starts and stops the configured message consumer and closes infrastructure resources on shutdown.

### 3. Dependency Assembly

Infrastructure assembly happens in `app/layer4_frameworks/__init__.py` through `create_infrastructure()`.

Current behavior:

- Creates the database connection when infra mode requires HANA and the necessary connection settings are present.
- Falls back to in-memory or mock persistence when a database connection is unavailable.
- Creates the message broker client for SAP Event Mesh or Kafka based on runtime mode, otherwise falls back to mock messaging.
- Builds the logger, monitor, repositories, messaging adapters, LLM provider, agent registry client, workflow client, permission provider, and graph checkpointer into a single `InfrastructureSet`.

On top of that, `bootstrap.py` assembles domain-facing services such as:

- `InputGuard`
- `OutputGuard`
- `IntentClassifier`
- `AgentSelector`
- `ContextEnricherService`
- `JourneyTracker`
- `ResponseFormatter`
- workflow completion summarization

Those dependencies are then exposed to application use cases and graph nodes.

### 4. Use Case Registration

`app/layer2_application/features/__init__.py` owns dynamic use-case discovery.

Current behavior:

- Scans feature packages under `app/layer2_application/features/`.
- Preserves legacy feature-level exports when a package exposes a direct use-case class.
- Scans nested `use_cases/*_use_case.py` modules inside each feature package.
- Registers use cases under keys such as `feature.action`.
- Instantiates each use case by matching constructor parameter names against the dependency dictionary prepared in `bootstrap.py`.

This means most new application behavior can be added by creating a new feature use case under the correct package and keeping constructor dependency names aligned with the available container keys.

### 5. Graph Registration

The orchestration graph is split across two layers:

- `app/layer2_application/graphs/orchestration_flow.py` owns workflow registration.
- `app/layer4_frameworks/graph/graph_builder.py` owns concrete LangGraph compilation.

`register_orchestration_flow()` wires the current nodes and transitions:

- `collect_permissions`
- `validate_input`
- `manage_conversation`
- `classify_intent`
- `dispatch_to_agent`
- `validate_output`
- response formatting variants
- `publish_events`
- `error_response`

The graph uses conditional routing to decide whether to:

- start from workflow prefetch or the main permissions path
- continue after input validation or reject early
- dispatch directly or classify intent first
- route unsupported and no-permission outcomes to dedicated response formatting
- publish events after both success and error paths

`compile_orchestration_graph()` wraps LangGraph's `StateGraph` with a small adapter so application registration stays behind the application-level graph builder contract.

## What Goes Where

### Root Files

- `bootstrap.py`: the only runtime composition root. Keep wiring here and keep business decisions out.
- `main.py`: FastAPI entrypoint and app lifecycle only.
- `requirements.txt`: pinned Python dependencies for this service.
- `pytest.ini`: test configuration.
- `docs/`: deeper operational and integration guidance.
- `plans/`: architecture and runtime design notes.
- `tests/`: unit-level coverage for domain, application, adapters, and frameworks.

### `app/layer1_domain/`

Put pure domain definitions here.

- `constants/`: domain defaults and messaging constants.
- `entities/`: business entities and domain services such as classifiers, guardrails, response formatting, conversation handling, and orchestration state.
- `enums/`: domain and workflow status enums.

This layer should remain framework-agnostic.

### `app/layer2_application/`

Put application orchestration logic here.

- `features/`: use cases for conversation management and orchestration flows.
- `graphs/`: application-owned node registration, routing edges, and graph flow definitions.
- `interfaces/`: contracts for repositories, providers, messaging, monitoring, graph building, and other application dependencies.
- `utils/`: application-level helper code that does not belong in the domain layer.

This layer coordinates business behavior without owning infrastructure implementations.

### `app/layer3_adapters/`

Put delivery adapters here.

- `controllers/restful/v1/` exposes the REST API surface.
- Controllers translate HTTP requests into use-case inputs and resolve use cases from the app container.

This layer should contain transport concerns only.

### `app/layer4_frameworks/`

Put concrete infrastructure here.

- `ai/`: LLM provider implementations and summarization helpers.
- `clients/`: external client adapters such as agent registry and workflow clients.
- `config/`: environment-backed settings.
- `graph/`: concrete graph compilation.
- `logging/` and `monitoring/`: observability implementations.
- `messaging/`: broker clients, publishers, consumers, queue builders, and handlers.
- `persistence/` and `repositories/`: concrete data access and checkpoint storage.
- `security/`: permission-provider implementations.

This layer adapts application contracts to real infrastructure and runtime integrations.

## Current Entry Points

### REST API

The current public API is exposed through Layer 3 REST controllers.

- `/health` returns a simple service health payload.
- `/orchestrate/async` accepts an orchestration request and delegates to the `orchestrate.orchestrate` use case.
- `/orchestrate/open-workspace` delegates to the `orchestrate.open_workspace` use case.
- `/conversations` routes are mounted separately for conversation-focused operations.

### Messaging

The service also reacts to asynchronous events.

- `bootstrap.py` registers an orchestrator response handler and a workflow event handler through the configured messaging queue builder.
- The FastAPI lifespan starts the message consumer on startup and stops it on shutdown when a consumer is configured.

## Architecture Notes

- Dependencies are intended to point inward only: Layer 4 to Layer 3 to Layer 2 to Layer 1.
- Domain and application layers should stay free of framework-specific runtime code.
- New orchestration behavior should usually be introduced by adding a use case, wiring it through the application graph or controller layer, and letting the composition root supply the concrete dependencies.
- The current repository includes both synchronous REST orchestration and asynchronous workflow-event handling, so changes often need to consider both request-response and event-driven paths.

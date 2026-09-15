# Worker SDK

A Python SDK for building workers in the AI Workflow Management platform. Workers are microservices that perform specific tasks (HTTP requests, wait/delay, data transformation, etc.) and integrate into the workflow execution engine via automatic registration, heartbeat, and a standardized REST API.

> **Building one right now?** This README is the reference. For the task-shaped guide —
> **PULL mode, several node types in one worker, and how input data actually arrives** —
> read **[`docs/worker-cookbook.md`](docs/worker-cookbook.md)**. It leads with a
> mode × capability table, because the capabilities are not the same in every mode and the
> gaps are silent (PULL cannot host multiple node types and does not resolve file
> references; HEADLESS has no task intake at all).

## Table of Contents

- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Creating a New Worker](#creating-a-new-worker)
  - [Step 1 - Scaffold the project](#step-1---scaffold-the-project)
  - [Step 2 - Create configuration](#step-2---create-configuration)
  - [Step 3 - Define input/output schemas](#step-3---define-inputoutput-schemas)
  - [Step 4 - Implement the use case](#step-4---implement-the-use-case)
  - [Step 5 - Create the entry point](#step-5---create-the-entry-point)
  - [Step 6 - Write tests](#step-6---write-tests)
  - [Step 7 - Dockerize](#step-7---dockerize)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [Worker Lifecycle](#worker-lifecycle)
- [Dependency Injection](#dependency-injection)
- [SDK Interfaces](#sdk-interfaces)
- [Input/Output Schema Format](#inputoutput-schema-format)
- [Running Modes](#running-modes)
- [Testing](#testing)
- [Deployment](#deployment)
- [Existing Workers](#existing-workers)

---

## Architecture

The SDK follows **Clean Architecture** with four layers. Dependencies point inward only — outer layers depend on inner layers, never the reverse.

```
┌──────────────────────────────────────────────────────┐
│  Layer 4 — Frameworks & Infrastructure               │
│  Config, Logger, Storage, Registry, Monitoring       │
├──────────────────────────────────────────────────────┤
│  Layer 3 — Adapters                                  │
│  REST controllers, DTOs, FastAPI app, headless runner│
├──────────────────────────────────────────────────────┤
│  Layer 2 — Application                               │
│  Use cases, interfaces (protocols), feature modules  │
├──────────────────────────────────────────────────────┤
│  Layer 1 — Domain                                    │
│  Entities, value objects, exceptions                 │
└──────────────────────────────────────────────────────┘
```

- **Layer 1 (Domain):** Pure data structures — `TaskRequest`, `TaskResponse`, `TaskStatus`, `WorkerStatus`, etc. No framework imports.
- **Layer 2 (Application):** Business logic via use cases. Defines `Protocol`-based interfaces (`ILogger`, `IMonitor`, `ITaskExecutor`, etc.). Feature modules are auto-discovered.
- **Layer 3 (Adapters):** FastAPI controllers, REST routes, DTOs. Bridges HTTP ↔ domain. Manages the worker server and headless runner.
- **Layer 4 (Frameworks):** Concrete implementations — `StandardLogger`, `PrometheusMonitor`, `HttpWorkerRegistry`, `LocalStorage`, `AppSettings`.

---

## Project Structure

```
worker-sdk/
├── worker_sdk/
│   ├── __init__.py                      # Public API exports
│   ├── bootstrap.py                     # Composition root & DI container
│   ├── runner.py                        # Entry point (SERVER / HEADLESS)
│   ├── main.py                          # Sample main executable
│   │
│   ├── layer1_domain/
│   │   ├── entities/                    # TaskRequest, TaskResponse, WorkerInfo, WorkerRegistration
│   │   ├── value_objects/               # TaskStatus, WorkerStatus, NodeClass, Port, WorkerCapability
│   │   └── exceptions.py
│   │
│   ├── layer2_application/
│   │   ├── features/
│   │   │   ├── execute_task/            # Core task execution use case
│   │   │   └── get_worker_info/         # Worker metadata use case
│   │   └── interfaces/                  # ILogger, IMonitor, ITaskExecutor, IStorage, etc.
│   │
│   ├── layer3_adapters/
│   │   └── controllers/
│   │       ├── restful/v1/              # REST routes & DTOs
│   │       ├── worker_server/           # FastAPI app factory (SERVER mode)
│   │       └── worker_headless/         # Async runner (HEADLESS mode)
│   │
│   └── layer4_frameworks/
│       ├── config/                      # AppSettings (pydantic-settings)
│       ├── logger/                      # StandardLogger
│       └── providers/
│           ├── registry/                # HttpWorkerRegistry
│           ├── monitoring/              # PrometheusMonitor
│           ├── storage/                 # LocalStorage
│           └── data_io/                 # LocalInputReader, LocalOutputWriter
│
├── tests/
│   ├── conftest.py                      # Shared fixtures (app_container, test client)
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── deployment/
│   └── Dockerfile
├── pyproject.toml
└── requirements.txt
```

---

## Getting Started

### Prerequisites

- Python >= 3.12
- pip

### Install the SDK

```bash
# From the worker-sdk directory
pip install -e .

# With dev dependencies
pip install -e ".[dev]"
```

### Run the sample worker

```bash
cd worker-sdk
python -m worker_sdk.main
```

---

## Creating a New Worker

This guide walks you through building a new worker from scratch. For reference, see the existing [http-request-worker](../http-request-worker/) and [wait-worker](../wait-worker/).

### Step 1 - Scaffold the project

Create the following directory structure next to the existing workers:

```
my-worker/
├── main.py
├── app/
│   ├── __init__.py
│   ├── layer2_application/
│   │   ├── __init__.py
│   │   └── features/
│   │       ├── __init__.py
│   │       └── execute_task/
│   │           ├── __init__.py
│   │           └── use_cases/
│   │               ├── __init__.py
│   │               └── execute_task_usecase.py
│   └── layer4_frameworks/
│       ├── __init__.py
│       └── config.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   └── unit/
│       ├── __init__.py
│       └── test_execute_task_usecase.py
├── requirements.txt
└── Dockerfile
```

> **Important:** The feature directory name must match the use case class name by convention.
> Directory `execute_task/` → class `ExecuteTaskUseCase` (PascalCase + `UseCase` suffix).

### Step 2 - Create configuration

```python
# app/layer4_frameworks/config.py
from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    APP_NAME: str = "My Custom Worker"
    WORKER_TYPE: str = "my_custom_worker"
    WORKER_VERSION: str = "1.0.0"
    WORKER_DESCRIPTION: str = "Performs custom operations"
    WORKER_NODE_CLASS: str = "BUSINESS"     # BUSINESS or TECHNICAL
    WORKER_ICON: str = "Cog"                # Icon name for the workflow UI
    WORKER_COLOR: str = "#3B82F6"           # Hex color for the workflow UI
    SERVER_PORT: int = 35001

    # Add worker-specific config here
    MY_CUSTOM_TIMEOUT: int = 30


settings = Settings()
```

All settings can be overridden by environment variables or a `.env` file.

### Step 3 - Define input/output schemas

Schemas define the form fields rendered in the workflow UI editor for this worker node.

```python
# Define at the top of your use case file, or in a separate schemas.py

MY_INPUT_SCHEMA = [
    {
        "key": "url",
        "outputType": "string",
        "fieldConfig": {
            "label": "Target URL",
            "description": "The URL to process",
            "fieldControl": "Input",
            "fieldWrapper": "FormItem",
            "controlProps": {"placeholder": "https://example.com"},
        },
        "default": "",
        "rules": {"isRequired": True},
    },
    {
        "key": "retry_count",
        "outputType": "number",
        "fieldConfig": {
            "label": "Retry Count",
            "description": "Number of retries on failure",
            "fieldControl": "Input",
            "fieldWrapper": "FormItem",
            "controlProps": {"type": "number"},
        },
        "default": 3,
    },
]

MY_OUTPUT_SCHEMA = [
    {
        "key": "result",
        "outputType": "string",
        "fieldConfig": {
            "label": "Result",
            "description": "The processing result",
            "fieldControl": "Input",
            "fieldWrapper": "FormItem",
        },
    },
    {
        "key": "success",
        "outputType": "boolean",
        "fieldConfig": {
            "label": "Success",
            "description": "Whether the operation succeeded",
            "fieldControl": "Input",
            "fieldWrapper": "FormItem",
        },
    },
]
```

### Step 4 - Implement the use case

```python
# app/layer2_application/features/execute_task/use_cases/execute_task_usecase.py
from typing import Any, Dict, List

from worker_sdk.layer2_application.interfaces.logger_interface import ILogger
from worker_sdk.layer2_application.interfaces.monitor_interface import IMonitor
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
    ExecuteTaskResult,
)
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus

# Schemas (can also be imported from a separate file)
MY_INPUT_SCHEMA: List[Dict[str, Any]] = [...]   # see Step 3
MY_OUTPUT_SCHEMA: List[Dict[str, Any]] = [...]   # see Step 3


class ExecuteTaskUseCase:
    """
    Your worker's core logic lives here.

    The SDK auto-discovers this class by convention:
    - Directory name: execute_task
    - Expected class: ExecuteTaskUseCase
    """

    def __init__(self, logger: ILogger, monitor: IMonitor, **kwargs):
        self.logger = logger
        self.monitor = monitor

    def execute(self, request: ExecuteTaskCommand) -> ExecuteTaskResult:
        """
        Called for each task execution.

        Args:
            request.task_id:        Unique task identifier
            request.action:         Action name (e.g., "execute")
            request.inputs:         Form inputs from the workflow UI
            request.parameters:     Additional parameters from the workflow engine
            request.correlation_id: Optional tracing correlation ID

        Returns:
            ExecuteTaskResult with status, outputs, and optional error
        """
        self.logger.info("Processing task", task_id=request.task_id)
        self.monitor.track("tasks_received", 1)

        try:
            url = request.inputs.get("url", "")
            retry_count = request.inputs.get("retry_count", 3)

            # ─── Your business logic here ───
            result_data = f"Processed {url} with {retry_count} retries"
            # ─────────────────────────────────

            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.SUCCESS,
                outputs={
                    "result": result_data,
                    "success": True,
                },
            )

        except Exception as e:
            self.logger.error("Task failed", task_id=request.task_id, error=str(e))
            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.ERROR,
                error=str(e),
                outputs={"success": False},
            )
```

**Injecting additional dependencies:** If your use case needs extra services (e.g., a database client), declare them as constructor parameters. The SDK's smart injection will wire them automatically if you pass them via `extra_dependencies`.

```python
class ExecuteTaskUseCase:
    def __init__(self, logger: ILogger, monitor: IMonitor, db_client: MyDbClient, **kwargs):
        ...
```

### Step 5 - Create the entry point

Pass your worker's `Settings` subclass instance as `app_settings` so its class
defaults (worker type, name, icon, color, etc.) reach the registration payload
the SDK sends to the executor.

```python
# main.py
import os
from worker_sdk import run_worker
from app.layer4_frameworks.config import settings as app_settings
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    MY_INPUT_SCHEMA,
    MY_OUTPUT_SCHEMA,
)

if __name__ == "__main__":
    run_worker(
        app_settings=app_settings,
        features_path=os.path.join(
            os.path.dirname(__file__), "app", "layer2_application", "features"
        ),
        base_module="app.layer2_application.features",
        extra_dependencies={
            "input_schema": MY_INPUT_SCHEMA,
            "output_schema": MY_OUTPUT_SCHEMA,
            # Add custom dependencies here:
            # "db_client": MyDbClient(settings.DB_URL),
        },
    )
```

When `app_settings` is provided, the SDK merges its field values into the
shared settings singleton **before** constructing any service, so the logger,
registry, HTTP endpoints, and registration payload all observe your worker's
values. The precedence is produced naturally by Pydantic at
`Settings()`-construction time: **env / `.env` &gt; your subclass default &gt;
base SDK default**. See [Configuration](#configuration) for details.

### Step 6 - Write tests

```python
# tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from worker_sdk.bootstrap import build_app_container
from worker_sdk.layer3_adapters.controllers.worker_server import create_worker_app


@pytest.fixture
def app_container():
    return build_app_container()


@pytest.fixture
def client(app_container):
    app = create_worker_app(app_container)
    return TestClient(app)
```

```python
# tests/unit/test_execute_task_usecase.py
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
)
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus


class StubLogger:
    def info(self, message, **kwargs): pass
    def error(self, message, **kwargs): pass
    def warning(self, message, **kwargs): pass
    def debug(self, message, **kwargs): pass


class StubMonitor:
    def track(self, metric, value): pass


def test_execute_task_success():
    use_case = ExecuteTaskUseCase(logger=StubLogger(), monitor=StubMonitor())

    result = use_case.execute(
        ExecuteTaskCommand(
            task_id="task-001",
            action="execute",
            inputs={"url": "https://example.com", "retry_count": 2},
        )
    )

    assert result.task_id == "task-001"
    assert result.status == TaskStatus.SUCCESS
    assert result.outputs["success"] is True


def test_execute_task_returns_error_on_failure():
    # Arrange a scenario that triggers an error in your logic
    use_case = ExecuteTaskUseCase(logger=StubLogger(), monitor=StubMonitor())

    result = use_case.execute(
        ExecuteTaskCommand(task_id="task-002", action="execute", inputs={})
    )

    assert result.task_id == "task-002"
    # Assert based on your implementation's behavior
```

Run tests:

```bash
cd my-worker
pytest tests/ -v --cov=app
```

### Step 7 - Dockerize

```dockerfile
# Dockerfile
FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN adduser --disabled-password --gecos '' appuser

WORKDIR /app

COPY --from=builder /install /usr/local
COPY . .

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 35001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "35001"]
```

```txt
# requirements.txt
-e ../worker-sdk
# Add worker-specific dependencies:
# httpx
# some-library
pytest
pytest-asyncio
pytest-cov
```

Build and run:

```bash
docker build -t my-worker:latest .
docker run -p 35001:35001 \
  -e APP_MODE=SERVER \
  -e REGISTRY_URL=http://registry:8000 \
  my-worker:latest
```

---

## API Reference

Every worker running in **SERVER** mode automatically exposes these endpoints:

### Infrastructure Endpoints

| Method | Path      | Description                |
|--------|-----------|----------------------------|
| GET    | `/health` | Health check               |
| GET    | `/ready`  | Readiness probe            |

**`GET /health`** response:
```json
{
  "status": "healthy",
  "worker_type": "my_custom_worker",
  "version": "1.0.0",
  "sdk_version": "1.0.0"
}
```

### Business Endpoints (prefix: `/api/v1`)

| Method | Path              | Description                      |
|--------|-------------------|----------------------------------|
| GET    | `/api/v1/info`    | Worker metadata & schemas        |
| POST   | `/api/v1/execute` | Execute a task (synchronous)     |
| POST   | `/api/v1/execute-async` | Execute a task (asynchronous, 202) |

**`POST /api/v1/execute`** request:
```json
{
  "task_id": "unique-task-id",
  "action": "execute",
  "inputs": {
    "url": "https://example.com",
    "retry_count": 3
  },
  "parameters": {},
  "correlation_id": "optional-trace-id"
}
```

**`POST /api/v1/execute`** response:
```json
{
  "task_id": "unique-task-id",
  "status": "success",
  "outputs": {
    "result": "...",
    "success": true
  },
  "error": null,
  "duration_ms": 142.5
}
```

**`POST /api/v1/execute-async`** accepts an additional `callback_url` field. The task runs in the background and the result is POSTed to the callback URL upon completion. Returns immediately with:

```json
{
  "task_id": "unique-task-id",
  "accepted": true
}
```

---

## Configuration

All settings are managed through environment variables (or a `.env` file) using [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).

### Precedence — env > code in worker > SDK default

The SDK honors a three-level precedence for every `Settings` field:

1. **Environment variable / `.env` file** — highest priority. Set at deploy time to override anything the code specifies.
2. **Worker subclass class default** — the "code in the specific worker". Declared as a class attribute on your worker's `Settings(_BaseSettings)` subclass and passed to `run_worker(app_settings=settings)`.
3. **Base SDK default** — the fallback declared on `worker_sdk.layer4_frameworks.config.app_config.Settings`.

The precedence is produced by Pydantic itself at `Settings()` instantiation time — env vars always override class defaults, and your subclass overrides the base class. `run_worker(app_settings=...)` then merges the resulting instance into the SDK's shared singleton so every subsequent service (logger, HTTP registry, registration payload, `/health` endpoint) sees the same effective values.

If you do not pass `app_settings`, the SDK falls back to its own base `Settings()` singleton — useful only for quick experiments. Production workers should always pass their subclass instance.

| Variable                       | Default               | Description                                     |
|--------------------------------|-----------------------|-------------------------------------------------|
| `APP_NAME`                     | `Worker SDK`          | Display name of the worker                      |
| `APP_MODE`                     | `SERVER`              | Running mode: `SERVER` or `HEADLESS`            |
| `SDK_VERSION`                  | `1.0.0`               | SDK protocol version                            |
| `WORKER_TYPE`                  | `generic`             | Unique worker type identifier                   |
| `WORKER_VERSION`               | `0.1.0`               | Worker version                                  |
| `WORKER_NAME`                  | `""`                  | Human-readable name (falls back to WORKER_TYPE) |
| `WORKER_DESCRIPTION`           | `""`                  | Worker description                              |
| `WORKER_NODE_CLASS`            | `BUSINESS`            | `BUSINESS` or `TECHNICAL`                       |
| `WORKER_ICON`                  | `Cog`                 | Icon name for the workflow UI                   |
| `WORKER_COLOR`                 | `#3B82F6`             | Hex color for the workflow UI                   |
| `WORKER_TAGS`                  | `""`                  | Comma-separated tags                            |
| `REGISTRY_URL`                 | `http://localhost:8000` | Worker registry service URL                   |
| `HEARTBEAT_INTERVAL_SECONDS`   | `30`                  | Heartbeat frequency in seconds                  |
| `SERVER_HOST`                  | `localhost`           | Server bind host                                |
| `SERVER_PORT`                  | `35000`               | Server bind port                                |
| `DATA_INPUT_PATH`              | `/tmp/worker/input`   | Local input data directory                      |
| `DATA_OUTPUT_PATH`             | `/tmp/worker/output`  | Local output data directory                     |

### Extending configuration

Override the `Settings` class in your worker to add custom fields **and** to
set worker-specific identity defaults. Place this in
`app/layer4_frameworks/config.py`:

```python
from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings

class Settings(_BaseSettings):
    APP_NAME: str = "My Worker"
    WORKER_TYPE: str = "my_worker"
    WORKER_NAME: str = "My Worker"
    WORKER_ICON: str = "Wrench"
    WORKER_COLOR: str = "#F59E0B"
    MY_API_KEY: str = ""
    MY_TIMEOUT: int = 60

settings = Settings()
```

Then pass this instance to `run_worker` in your `main.py`:

```python
from app.layer4_frameworks.config import settings as app_settings
from worker_sdk import run_worker

run_worker(app_settings=app_settings, ...)
```

Without the `app_settings=` argument, your subclass is never consulted and the
SDK registers the worker with its base defaults (`generic` / `Cog` / etc.).

---

## Worker Lifecycle

```
  ┌─────────────┐
  │  Startup    │
  │  register() │──────────┐
  └──────┬──────┘          │
         │                 ▼
         │         ┌──────────────┐
         │         │  Heartbeat   │◄────┐
         │         │  loop (30s)  │─────┘
         │         └──────┬───────┘
         │                │ (re_register signal)
         │                ▼
         │         ┌──────────────┐
         │         │ Re-register  │
         │         └──────────────┘
         │
  ┌──────┴──────┐
  │  Shutdown   │
  │ deregister()│
  └─────────────┘
```

1. **Startup:** The worker calls `register()` on the registry service, sending its type, version, endpoint, schemas, and capabilities. It receives a `worker_id`.
2. **Heartbeat:** Every `HEARTBEAT_INTERVAL_SECONDS`, the worker sends a heartbeat with its current status (`HEALTHY`). If the registry responds with `re_register: true`, the worker re-registers.
3. **Shutdown:** On SIGTERM/SIGINT, the worker cancels the heartbeat loop and calls `deregister()`.

Registry API paths:
- Register: `POST {REGISTRY_URL}/api/v1/workers/register`
- Heartbeat: `POST {REGISTRY_URL}/api/v1/workers/{worker_id}/heartbeat`
- Deregister: `POST {REGISTRY_URL}/api/v1/workers/{worker_id}/deregister`

---

## Dependency Injection

The SDK uses a **composition root** pattern (`bootstrap.py`) with smart constructor injection.

### How it works

1. Infrastructure dependencies are created:

```python
available_dependencies = {
    "logger":          StandardLogger(),
    "monitor":         PrometheusMonitor(),
    "storage":         LocalStorage(),
    "input_reader":    LocalInputReader(),
    "output_writer":   LocalOutputWriter(),
    "worker_registry": HttpWorkerRegistry(),
}
```

2. `extra_dependencies` from `run_worker()` are merged in (e.g., `input_schema`, `output_schema`, custom services).

3. Feature use cases are auto-discovered from the `features/` directory.

4. For each use case, the SDK inspects its `__init__` signature and injects only the parameters it declares:

```python
# If your use case declares:
class ExecuteTaskUseCase:
    def __init__(self, logger: ILogger, monitor: IMonitor, **kwargs):
        ...

# The SDK inspects the signature and injects: logger, monitor
# Other dependencies (storage, input_reader, etc.) are skipped
```

### Passing custom dependencies

```python
run_worker(
    extra_dependencies={
        "input_schema": MY_INPUT_SCHEMA,
        "output_schema": MY_OUTPUT_SCHEMA,
        "my_service": MyService(),          # Available to any use case that declares it
    },
)
```

---

## SDK Interfaces

All interfaces use Python `Protocol` for structural typing — no need to inherit, just match the signature.

### ILogger

```python
class ILogger(Protocol):
    def info(self, message: str, **kwargs) -> None: ...
    def error(self, message: str, **kwargs) -> None: ...
    def warning(self, message: str, **kwargs) -> None: ...
    def debug(self, message: str, **kwargs) -> None: ...
```

### IMonitor

```python
class IMonitor(Protocol):
    def track(self, metric: str, value: float) -> None: ...
```

### ITaskExecutor

```python
class ITaskExecutor(Protocol):
    def execute(self, request: TaskRequest) -> TaskResponse: ...
```

### IStorage

```python
class IStorage(Protocol):
    def save(self, filename: str, data: bytes) -> None: ...
    def get(self, filename: str) -> bytes: ...
```

### IInputReader / IOutputWriter

Async interfaces for reading input data and writing output data. See the interface files for full method signatures.

### IWorkerRegistry

```python
class IWorkerRegistry(Protocol):
    async def register(self, registration: WorkerRegistration) -> str: ...
    async def heartbeat(self, worker_id: str, status: WorkerStatus) -> dict: ...
    async def deregister(self, worker_id: str) -> None: ...
```

---

## Input/Output Schema Format

Schemas define form fields for the workflow UI editor. Each field follows this structure:

```python
{
    "key": "field_name",                # Unique field identifier (maps to inputs dict key)
    "outputType": "string",             # Data type: string, number, boolean, object, array
    "fieldConfig": {
        "label": "Field Label",         # Display label
        "description": "Help text",     # Tooltip/description
        "fieldControl": "Input",        # Control type: Input, Select, Textarea, Switch, etc.
        "fieldWrapper": "FormItem",     # Wrapper component
        "controlProps": {               # Props passed to the control component
            "placeholder": "Enter value",
            "type": "number",           # HTML input type
        },
    },
    "default": "default_value",         # Default value
    "rules": {
        "isRequired": True,             # Validation: required field
    },
    "show_when": {                      # Conditional visibility
        "field": "other_field",
        "value": "some_value",
    },
}
```

---

## Running Modes

### SERVER mode (default)

Starts a FastAPI HTTP server. Tasks are received via REST API calls.

```bash
APP_MODE=SERVER python main.py
```

- Binds to `SERVER_HOST:SERVER_PORT`
- Registers with the worker registry
- Sends periodic heartbeats
- Exposes `/health`, `/ready`, `/api/v1/info`, `/api/v1/execute`, `/api/v1/execute-async`

### HEADLESS mode

Runs as a standalone async process without an HTTP server. Useful for event-driven workers (Kafka consumers, queue processors, etc.).

```bash
APP_MODE=HEADLESS python main.py
```

- No HTTP server started
- Still registers with the registry (endpoint reported as `"headless"`)
- Still sends heartbeats
- Task ingestion must be implemented separately (e.g., Kafka consumer loop)

### PULL mode

Leases tasks from the executor instead of being pushed to — for workers the executor cannot
reach (no route, laptop, locked-down network). No HTTP server; the long-poll is the intake and
the primary liveness signal.

```bash
APP_MODE=PULL python main.py
```

- Registers with `endpoint="pull"` and `delivery_mode="pull"`
- Long-polls `POST /api/v1/workers/type/{worker_type}/tasks/lease`, reports to
  `POST /api/v1/tasks/callback` with the lease token in the `X-Lease-Token` header
- Tuned by `LEASE_*` settings; turn on `LEASE_RENEW_ENABLED` for tasks that can outlive the
  lease TTL

Multiple node types work here as they do in SERVER — `run_worker(node_types=[...])` registers
each type as its own pull node and gives it its own lease loop (the executor partitions the
lease queue by `(tenant, worker_type)`), all polled concurrently.

**PULL is still not feature-equivalent to SERVER**: no worker functions, and
it does **not** resolve `__file_ref` inputs or stream large outputs for you. See
[`docs/worker-cookbook.md`](docs/worker-cookbook.md) §0 and §3 before choosing it.

> `WORKER_DELIVERY_MODE` is declared in `Settings` but never read. `APP_MODE=PULL` is the
> switch.

---

## Testing

### Project setup

```bash
pip install -e ".[dev]"
```

### Running tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=app --cov-report=term-missing

# Run only unit tests
pytest tests/unit/ -v
```

### Test patterns

**Unit test — use case with stub dependencies:**

```python
class StubLogger:
    def info(self, message, **kwargs): pass
    def error(self, message, **kwargs): pass
    def warning(self, message, **kwargs): pass
    def debug(self, message, **kwargs): pass

class StubMonitor:
    def track(self, metric, value): pass

def test_my_use_case():
    uc = ExecuteTaskUseCase(logger=StubLogger(), monitor=StubMonitor())
    result = uc.execute(ExecuteTaskCommand(task_id="t1", action="execute", inputs={...}))
    assert result.status == TaskStatus.SUCCESS
```

**Integration test — HTTP endpoints with TestClient:**

```python
def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_execute(client):
    response = client.post("/api/v1/execute", json={
        "task_id": "t1",
        "action": "execute",
        "inputs": {"url": "https://example.com"},
    })
    assert response.status_code == 200
    assert response.json()["status"] == "success"
```

---

## Deployment

### Docker build & run

```bash
# Build
docker build -t my-worker:latest -f Dockerfile .

# Run in SERVER mode
docker run -p 35000:35000 \
  -e APP_MODE=SERVER \
  -e WORKER_TYPE=my_worker \
  -e REGISTRY_URL=http://registry:8000 \
  -e SERVER_HOST=0.0.0.0 \
  my-worker:latest

# Run in HEADLESS mode
docker run \
  -e APP_MODE=HEADLESS \
  -e WORKER_TYPE=my_worker \
  -e REGISTRY_URL=http://registry:8000 \
  my-worker:latest
```

### Health checks (for Kubernetes / Docker Compose)

```yaml
# docker-compose example
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:35000/health"]
  interval: 10s
  timeout: 5s
  retries: 3
```

```yaml
# Kubernetes liveness/readiness probes
livenessProbe:
  httpGet:
    path: /health
    port: 35000
  periodSeconds: 10
readinessProbe:
  httpGet:
    path: /ready
    port: 35000
  periodSeconds: 5
```

---

## Existing Workers

These workers serve as reference implementations:

| Worker                                              | Type            | Description                                |
|-----------------------------------------------------|-----------------|--------------------------------------------|
| [http-request-worker](../http-request-worker/)      | `http_request`  | Makes HTTP requests (GET, POST, PUT, etc.) |
| [wait-worker](../wait-worker/)                      | `wait`          | Fixed delay, datetime wait, manual approval|

Study their `execute_task_usecase.py` for real-world examples of input/output schema design and task execution patterns.

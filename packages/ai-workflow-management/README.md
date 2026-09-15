# AI Workflow Management

The core **AI workflow orchestration** platform for SimpleMDG — the control plane that
plans, runs, and coordinates AI workflows (Python, FastAPI + Socket.IO + gRPC, LangGraph).

## Layout

| Path | Purpose |
|---|---|
| [`workflow/`](workflow/) | The workflow services — including the **workflow-control-plane-service** (core orchestration) |
| [`workflow_proto/`](workflow_proto/) | gRPC protobuf definitions shared across workflow services |
| [`GETTING_STARTED.md`](GETTING_STARTED.md) | Local dev setup (single root `.venv` + VSCode `launch.json`) |

## Getting started

Follow [`GETTING_STARTED.md`](GETTING_STARTED.md) — it walks through opening the workspace
at this folder, the shared root `.venv`, and the pre-configured debug/launch configs for
every service.

> Some services here have **two entry points** — e.g. the workflow control-plane service
> has `main.py` (FastAPI + Socket.IO + gRPC) *and* `app/executor_main.py` (standalone
> executor, `python -m app.executor_main`). Confirm which entry point your change affects.

Tests are `pytest` per service. See the repo [`CLAUDE.md`](../../../CLAUDE.md) for
monorepo-wide conventions.

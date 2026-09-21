# Agent subsystem

Python (LangGraph-based) AI agents for SimpleMDG, plus the shared **Agent SDK** they are
built on. Each agent follows the repo's 4-layer clean-architecture pattern (see the SDK's
[clean-architecture tutorial](agent-sdk/docs/03-building-agents/clean-architecture-tutorial.md)).

## What's here

| Directory | Purpose |
|---|---|
| [`agent-sdk/`](agent-sdk/) | Shared SDK for building agents — start at [`agent-sdk/docs/`](agent-sdk/docs/); runnable samples under `agent-sdk/examples/` |
| [`base-agent/`](base-agent/) | Base agent foundation other agents extend |
| [`business-agent/`](business-agent/) | Business-domain agent |
| [`orchestrator/`](orchestrator/) | Coordinates multi-agent workflows |
| [`agent-registry/`](agent-registry/) | Registry of available agents |
| [`executor-runtime-service/`](executor-runtime-service/) | Runtime that executes agents |
| [`file-agent/`](file-agent/) | File-handling agent |
| [`governance-schema-agent/`](governance-schema-agent/) | Governance / schema agent |
| [`profile-manager-agent/`](profile-manager-agent/) | Profile-management agent |
| [`validation-rule-agent/`](validation-rule-agent/) | Validation-rule agent |
| [`docs/`](docs/) | Subsystem documentation |

## Getting started

New agents are built on the SDK — read [`agent-sdk/docs/02-quickstart/`](agent-sdk/docs/02-quickstart/)
first. Agents are Python services (FastAPI / LangGraph); run and test each from its own
directory:

```bash
pip install -e ".[dev]"
pytest
```

See the repo [`CLAUDE.md`](../../../CLAUDE.md) for the monorepo-wide conventions.

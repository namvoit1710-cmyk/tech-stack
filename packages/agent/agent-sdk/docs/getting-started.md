# Getting Started

> **This page is a compatibility stub.**
> The canonical getting-started content has moved to [02 — Quick Start](02-quickstart/README.md).

---

## Where to go

| Goal | Go to |
|---|---|
| Install and run your first agent in 5 minutes | [02 — Quick Start → README](02-quickstart/README.md) |
| Step-by-step first agent walkthrough | [02 — Quick Start → First Agent](02-quickstart/first-agent.md) |
| Understand what the SDK does for you | [02 — Quick Start → SDK Mental Model](02-quickstart/mental-model.md) |
| Choose between `ToolAgentBuilder`, `AgentGraphBuilder`, `FlowGraphBuilder` | [02 — Quick Start → Choose Your Builder](02-quickstart/choose-your-builder.md) |
| Full env var / settings reference | [Reference → Configuration](05-reference/configuration.md) |

---

## Quick reference (preserved)

### Install

```bash
pip install -e ".[dev]"
```

### Minimum `.env`

```
OPENAI_API_KEY=sk-...
APP_MODE=SERVER
```

### Three builder paths

| Path | Builder | When to use |
|------|---------|-------------|
| A — Minimal | `ToolAgentBuilder` | Supply a list of tools; the SDK wires the full tool-calling loop |
| B — Custom graph | `AgentGraphBuilder` | Full control over nodes and edges; still avoids direct LangGraph imports |
| C — Declarative flow | `FlowGraphBuilder` | Config-driven sequential flows with conditional branching |

For the full guide, see [02 — Quick Start → Choose Your Builder](02-quickstart/choose-your-builder.md) and [04 — Features → Builders](04-features/builders.md).

---

[← README](../README.md) | [Docs index](README.md)

# Agent SDK — Documentation

[← Back to SDK root](../README.md)

---

## Documentation Structure

> **Canonical content** lives in the numbered section folders below.
> Existing flat files (`docs/*.md`) are retained as compatibility stubs and link to the canonical pages.
> Do not add new long-form content to the flat files; place it in the appropriate section folder instead.

---

## Sections

| # | Section | Contents |
|---|---|---|
| 01 | [Overview](01-overview/README.md) | What the SDK is, four-layer architecture diagram, capability matrix, feature highlights |
| 02 | [Quick Start](02-quickstart/README.md) | Install, environment setup, run first agent, call `/api/v1/execute`, example file index |
| 03 | [Building Agents](03-building-agents/README.md) | Builder paths, graph patterns, HITL, subgraphs, clean-architecture tutorial with public examples |
| 04 | [Features](04-features/README.md) | Checkpointing, transports, remote agents, context budget, workflow events, DI cookbook |
| 05 | [Reference](05-reference/README.md) | Full API surface, configuration variables, architecture module map, testing and troubleshooting |
| 06 | [Onboarding](06-onboarding/README.md) | New-developer orientation: repo layout, core concepts, first PR walkthrough, [presentation overview](06-onboarding/agent-sdk-for-new-developers.md) |

---

## Reader Routes

### New to the SDK?

**Recommended entry point for new developers:**
→ **[Agent SDK for New Developers](06-onboarding/agent-sdk-for-new-developers.md)** — what the SDK solves, all builder paths, clean-architecture overview, feature summary, and where to go next.

Or follow the manual path:

1. Read [Overview](01-overview/README.md) — understand what the SDK does and how it is structured.
2. Follow [Quick Start](02-quickstart/README.md) — get an agent running in 5 minutes.
3. Read [Overview → Core Concepts](01-overview/core-concepts.md) — understand state, nodes, and the run lifecycle.

### Building your first agent?

1. [Quick Start](02-quickstart/README.md) — tool agent in 5 minutes.
2. [Building Agents](03-building-agents/README.md) — choose your builder path, wire nodes, add pre/post nodes.
3. [Features → Checkpointing](04-features/checkpointing.md) — persist state if you need HITL or multi-turn conversations.

### Adding human approval to a workflow?

→ [Building Agents](03-building-agents/README.md) (HITL section) and [Features → Checkpointing](04-features/checkpointing.md).

### Switching to Kafka / Event Mesh?

→ [Features → Transports](04-features/transports.md).

### Calling another agent as a tool?

→ [Features → Remote Agents](04-features/remote-agents.md).

### Building a multi-agent supervisor?

→ [Features → Remote Agents — Dynamic Discovery](04-features/remote-agents.md#dynamic-agent-discovery).

### Looking up a class or function?

→ [Reference → API Reference](05-reference/api-reference.md).

### Deploying to SAP BTP?

→ [Reference → Configuration](05-reference/configuration.md) (`GET_FROM_VCAP`, `INFRA_MODE`).

---

## Migration Strategy

When adding or expanding documentation:

1. **New content** always goes into the appropriate numbered section folder (`01-overview/`, `02-quickstart/`, etc.).
2. **Existing flat files** (`docs/*.md`) remain as compatibility stubs. They may be updated to add stub redirects or short summaries that link into the canonical section pages.
3. **Never duplicate** long-form reference content between a flat file and a section page. The section page is the single source of truth.
4. **Root README** (`../README.md`) is a landing page only — it links to section indexes and contains only the minimal quick-start snippet. Do not add reference tables or full API listings there.

---

[← Back to SDK root](../README.md)

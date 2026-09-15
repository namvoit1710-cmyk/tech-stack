# Workflow — Enhance Node & Workflow to Support Agents

## Epic

**As a** workflow designer building AI-assisted automations,
**I want to** declare, on each node and on the workflow itself, *what action is performed*, *how it should be carried out* (instructions for an AI), and *which agents are allowed to carry it out*,
**so that** the agent runtime can pick the right agent for each node, hand it a clear brief, and keep execution constrained to the agents the designer has authorized.

---

## Context

### What already exists

[`WorkflowNode`](../app/layer1_domain/entities/workflow.py) today carries human-readable metadata:

| Field | Purpose today |
|---|---|
| `description` | Short description of what the node is |
| `instruction` | Human-readable instruction text (free-form, not yet consumed by any runtime) |
| `tags`, `icon`, `color` | Presentation metadata |
| `worker_id`, `node_definition_id`, `input_binding` | Runtime routing — where the work actually goes |

[`Workflow`](../app/layer1_domain/entities/workflow.py) carries `description` and `purpose`, but nothing equivalent to `instruction` and no notion of which agents may run inside it.

### Gaps

1. **No `action` field.** `description` explains *what the node is*; there is no place for the designer to state, in one line, *the real action performed* (e.g. `"summarize_email_thread"`, `"extract_invoice_fields"`). The agent runtime needs this as an explicit, machine-addressable contract, not prose buried inside `description`.
2. **Node `instruction` is orphan data.** The field exists, but nothing reads it. We want it to become the authoritative prompt-style brief the agent runtime passes to the selected agent.
3. **No `agents` allow-list.** Today any agent-worker can be invoked for any TASK node that routes to it. Designers need a way to restrict a given node to a named set of agents so that (a) the agent-selection system knows its candidate pool and (b) the workflow remains auditable.
4. **No workflow-level equivalents.** The same three pieces of metadata — action, instruction, agents — need to exist at the workflow scope, so sub-workflows invoked via the `WORKFLOW` node, and top-level runs, can be described and routed the same way.

### Why now

The `agent-worker` ([`workflow/worker/agent-worker`](../../../worker/agent-worker)) already speaks to LLM providers (OpenAI, Anthropic) but is dispatched like any other worker — the engine has no first-class concept of "which agent should handle this node." Adding these fields is the precondition for an agent-selection layer that reads node metadata and routes to the right agent at dispatch time.

---

## Goals

1. Add `action`, keep `instruction`, and add `agents` on `WorkflowNode`.
2. Add the same three fields on `Workflow`.
3. Expose all three in the REST API (create, read, update) and in the workflow/node JSON schemas.
4. Surface the fields in variable-suggestion / node-palette APIs so the UI can render them.

**Non-goals**

- Agent selection / routing logic itself. This story only defines the data the selection layer will consume.
- Authentication or authorization on agent identities. The `agents` list is a declarative allow-list keyed by agent id; enforcement is a follow-up.
- Backward-compatibility shims. Fields default to empty and are additive — existing workflows continue to work without migration.

---

## Data Model Changes

### Node — new and repurposed fields

| Field | Type | Default | Description |
|---|---|---|---|
| `action` | `string` | `""` | One-line, machine-addressable label for the real action the node performs (e.g. `"summarize_document"`, `"classify_ticket"`). Stable identifier used by the agent-selection layer. |
| `instruction` | `string` | `""` | *Existing field, repurposed.* Free-form prompt-style brief the agent runtime passes to the selected agent. May reference node inputs via `{{$node.field}}` syntax, resolved by the existing `VariableResolver`. |
| `agents` | `list[AgentRef]` | `[]` | Allow-list of agent identities that may run this node. Empty list ⇒ no agent constraint (any registered agent-worker is eligible). |

#### `AgentRef` value object

| Field | Type | Default | Description |
|---|---|---|---|
| `agent_id` | `string` | *(required)* | Stable id of the agent (matches the agent-worker's registered identity). |
| `role` | `string` | `""` | Optional human-readable role in this node (e.g. `"primary"`, `"reviewer"`). Presentational only. |
| `priority` | `int` | `0` | Higher values ranked first when the selection layer picks one of several eligible agents. |

### Workflow — same additions at workflow scope

| Field | Type | Default | Description |
|---|---|---|---|
| `action` | `string` | `""` | One-line label for the workflow's overall action. Used when the workflow is invoked as a sub-workflow by a `WORKFLOW` node. |
| `instruction` | `string` | `""` | Prompt-style brief describing how the workflow as a whole should be carried out. |
| `agents` | `list[AgentRef]` | `[]` | Workflow-scope allow-list. A node inherits this list if its own `agents` is empty; a non-empty node list overrides (it does not merge). |

---

## User Stories

### US-1 — Designer sets the action a node performs

**As a** workflow designer,
**I want to** set an `action` string on a node,
**so that** the agent-selection layer and downstream observability tools have a stable, machine-addressable label for what the node does.

**Acceptance criteria**
- `action` is optional, defaults to `""`, and is persisted on create/update.
- `action` is returned verbatim in `GET /workflows/{id}` and any node-detail endpoint.
- The workflow-example and node-palette seed fixtures include `action` on at least one sample node.

---

### US-2 — Designer writes an instruction the agent runtime will execute

**As a** workflow designer,
**I want to** write a free-form instruction on a node (how to fill its inputs, what it should achieve when it runs),
**so that** an AI agent assigned to the node has an authoritative brief to follow.

**Acceptance criteria**
- `instruction` accepts template expressions (`{{$upstream_node.field}}`, `$metadata.run_id`, `$ref:file_id`) and resolves them through the existing `VariableResolver` strategy chain at dispatch time.
- Resolution errors (missing variable, malformed expression) surface as a `TASK_INSTRUCTION_RESOLUTION_FAILED` error code — they do *not* silently pass an unresolved template to the agent.
- When the engine dispatches a TASK to an agent-worker, the resolved instruction is included in the task payload under `instruction`.

---

### US-3 — Designer restricts a node to specific agents

**As a** workflow designer,
**I want to** attach an allow-list of agents to a node,
**so that** only agents I have pre-approved can run that node, and the selection layer has a bounded candidate pool.

**Acceptance criteria**
- `agents` accepts a list of `AgentRef` objects; duplicates (same `agent_id`) are rejected on validation.
- Empty list ⇒ unconstrained (any registered agent-worker is eligible).
- At dispatch time, if `agents` is non-empty and no listed `agent_id` is currently registered/healthy, the task fails fast with `NO_ELIGIBLE_AGENT` — it does not fall back to an unlisted agent.
- The selection layer orders eligible agents by `priority` descending, then by registration order.

---

### US-4 — Designer sets the workflow's overall action

**As a** workflow designer,
**I want to** set `action` on the workflow itself,
**so that** a `WORKFLOW` node invoking this workflow, and the run list UI, can display a consistent one-line label.

**Acceptance criteria**
- Mirrors US-1 at the workflow level. Returned by `GET /workflows` list items and `GET /workflows/{id}`.

---

### US-5 — Designer writes a workflow-level instruction

**As a** workflow designer,
**I want to** write a workflow-level `instruction` explaining the workflow's goal and how it should be pursued,
**so that** agents operating inside it (and future workflow-level AI planners) have shared context about the overall objective.

**Acceptance criteria**
- Workflow `instruction` is passed in the task payload alongside the node instruction, under a separate key (`workflow_instruction`), so agents can distinguish the two.
- Template expressions in workflow instruction resolve against `$metadata.*` only (workflow-level context has no upstream node to pull from).

---

### US-6 — Workflow scope defines the default agent allow-list

**As a** workflow designer,
**I want to** declare a workflow-level `agents` allow-list,
**so that** I can authorize a single set of agents once and have every node inherit it, without repeating the list on each node.

**Acceptance criteria**
- A node with empty `agents` inherits the workflow `agents` list verbatim at dispatch time.
- A node with a non-empty `agents` list **overrides** (does not merge with) the workflow list.
- The resolved (post-inheritance) list is logged to the task audit record so runtime selection is reproducible from the stored data alone.

---

## Example — Node and Workflow with the new fields

```jsonc
{
  "id": "wf-customer-followup",
  "name": "Customer Follow-up",
  "action": "customer_followup",
  "instruction": "Pick up support tickets marked 'awaiting-followup', draft a reply tailored to the customer's last message, and queue it for human review.",
  "agents": [
    { "agent_id": "agent-support-writer-v2", "role": "primary", "priority": 10 },
    { "agent_id": "agent-support-writer-v1", "role": "fallback", "priority": 1 }
  ],

  "nodes": [
    {
      "id": "summarize-thread",
      "name": "Summarize Thread",
      "type": "TASK",
      "node_class": "BUSINESS",
      "description": "Produces a three-sentence summary of the ticket thread.",
      "action": "summarize_document",
      "instruction": "Summarize the ticket thread at {{$fetch-ticket.response_body.thread}} in at most three sentences. Preserve customer sentiment. Return plain text.",
      "agents": [
        { "agent_id": "agent-summarizer-gpt4o", "priority": 10 }
      ],
      "worker_id": "agent-worker",
      "parameters": { }
    },
    {
      "id": "draft-reply",
      "name": "Draft Reply",
      "type": "TASK",
      "node_class": "BUSINESS",
      "description": "Drafts the follow-up reply.",
      "action": "draft_customer_reply",
      "instruction": "Using the summary at {{$summarize-thread.output.result}}, draft a reply to the customer. Match the tone of their last message. Sign off as 'Support Team'.",
      "agents": [],
      "worker_id": "agent-worker",
      "parameters": { }
    }
  ]
}
```

In this example:
- `draft-reply` has `agents: []` and therefore inherits the workflow list (`agent-support-writer-v2` preferred, `-v1` fallback).
- `summarize-thread` overrides with its own list; the workflow list is *not* merged in.

---

## Out of Scope / Follow-ups

- Agent-selection algorithm (priority, health, cost, load). This story defines the data; routing is a separate story.
- UI for editing `action` / `instruction` / `agents` in the node inspector — covered in a frontend user story once the backend contract is merged.
- Runtime enforcement of per-agent capabilities (e.g. "this agent may only read, not write"). Capability metadata lives on the agent-worker registration, not on the node.
- Versioning of agent identities. For now `agent_id` is an opaque string; a future story can introduce pinned versions (`agent_id@1.2.0`).

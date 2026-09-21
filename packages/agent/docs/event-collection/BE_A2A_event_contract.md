# Backend Event Contract

This document reflects the event shapes currently implemented across:

- `business-agent`
- `orchestrator`
- `executor-runtime-service`

It replaces the older idealized contract. The current code does not use one perfectly uniform envelope across all producers.

## Current Routing

| Producer | Event type | Consumer | Actual use |
| --- | --- | --- | --- |
| Orchestrator | `conversation.plan.created` | Business Agent | Tell the business agent to start planning for the current conversation |
| Orchestrator | `sub:user:chat` | Business Agent | Continue a child conversation |
| Business Agent | `agent.request.executor` | Executor Runtime | Hand off one executable step at a time |
| Executor Runtime | `executor.step.status` | Business Agent | Report `processing`, `success`, or `error` for one step |

## 1. Envelope Shapes By Producer

### 1.1 Orchestrator -> Business Agent

Published from `orchestrator/app/layer2_application/features/orchestrate_graph/use_cases/orchestrate_graph_use_case.py`.

```json
{
  "id": "019f...",
  "type": "conversation.plan.created",
  "conv_id": "019f...",
  "payload": {
    "from": "orchestrator",
    "to": "agent",
    "steps": [],
    "node_info": null,
    "workflow_id": null
  },
  "correlation_id": "019f..."
}
```

Notes:

- The root field is `id`, not `message_id`.
- `conversation.plan.created` currently carries no task list in `payload.steps`.
- The business agent loads the actual task plan from shared state, not from this event body.

### 1.2 Business Agent -> Executor Runtime

Published from `business-agent/app/layer2_application/features/main_agent/use_cases/create_plan_usecase.py`.

```json
{
  "message_id": "019f...",
  "type": "agent.request.executor",
  "conv_id": "019f...",
  "correlation_id": "019f...",
  "payload": {
    "from": "agent.<business-agent-id>",
    "to": "executor.runtime",
    "steps": [
      {
        "id": "019f...",
        "execute_by": "tool.<uuid>",
        "goal": "Run workflow start",
        "message": "Run workflow start",
        "next_step_id": "019f...",
        "status": "PENDING",
        "uploaded_file_ids": [],
        "input_data": {},
        "output_data": {}
      }
    ],
    "node_info": null,
    "workflow_id": null,
    "type": "step_batch",
    "batch_id": "019f..."
  },
  "metadata": null
}
```

Notes:

- This service uses `message_id`, not `id`.
- The business agent currently sends one step per request.
- `payload.to` must be `executor.runtime`.
- `execute_by` must match `tool.<uuid>` or `agent.<uuid>` for executor validation to pass.

### 1.3 Executor Runtime -> Business Agent

Published from `executor-runtime-service/app/layer4_frameworks/messaging/direct_step_status_publisher.py`.

```json
{
  "id": "019f...",
  "type": "executor.step.status",
  "message_id": "019f...",
  "correlation_id": "019f...",
  "conv_id": "019f...",
  "payload": {
    "from": "executor.runtime",
    "to": "agent.<business-agent-id>",
    "type": "step",
    "steps": [
      {
        "id": "019f...",
        "execute_by": "tool.<uuid>",
        "goal": "Run workflow start",
        "message": "Human-readable step message",
        "status": "processing",
        "input_data": {},
        "output_data": {},
        "error": ""
      }
    ]
  }
}
```

Notes:

- This payload includes both `id` and `message_id`.
- `payload.type` is `"step"`, but the step data is still wrapped in `payload.steps[0]`.
- The business agent review flow only reads the first step in `payload.steps`.

## 2. Executor Validation Rules

Implemented in `executor-runtime-service/app/layer2_application/features/validate_step_batch/use_cases/validate_step_batch_use_case.py`.

- `type` must be `agent.request.executor`
- `message_id` is required
- `correlation_id` is required
- `conv_id` is required
- `payload.type` must be `step_batch`
- `payload.from` is required
- `payload.to` must be `executor.runtime`
- `payload.steps` must be a non-empty array
- `step.execute_by` must match `tool.<uuid>` or `agent.<uuid>`
- `step.input_data` must be an object when present
- `step.output_data` must be an object when present

## 3. Current Step Lifecycle

### 3.1 Planning trigger

1. Orchestrator stores or updates conversation shared state.
2. Orchestrator publishes `conversation.plan.created`.
3. Business agent receives the event and builds a child execution plan from shared state.

### 3.2 Executor handoff

1. Business agent saves child steps into shared state.
2. Business agent sets `metadata.current_task_id`.
3. Business agent publishes `agent.request.executor` for the first ready step.

### 3.3 Execution feedback

1. Executor runtime publishes `executor.step.status` with `status=processing`.
2. Executor runtime resolves the capability and executes locally or delegates to a technical agent.
3. Executor runtime publishes `executor.step.status` with `status=success` or `status=error`.
4. Business agent updates task state and either dispatches the next step or sends a final summary to FE.

## 4. Technical Agent Delegation

The executor runtime can delegate a step when capability resolution returns `TECHNICAL_AGENT`.

That path is implemented in:

- `executor-runtime-service/app/layer4_frameworks/services/technical_agent_step_executor.py`
- `executor-runtime-service/app/layer4_frameworks/messaging/technical_agent_reply_handler.py`

Observed behavior:

- The executor generates a new delegated correlation id.
- It sends an `AgentCallRequest`, not the business-agent `QueueEvent` type.
- It injects `message_overrides` so the downstream technical agent can receive a payload shaped like:

```json
{
  "message_id": "019f...",
  "conv_id": "019f...",
  "conversation_id": "019f...",
  "payload": {
    "from": "executor.runtime",
    "to": "<agent-id>",
    "type": "step",
    "step": {
      "id": "019f...",
      "execute_by": "agent.<uuid>",
      "goal": "Delegate this step",
      "message": "Delegate this step",
      "uploaded_file_ids": [],
      "input_data": {},
      "output_data": {}
    }
  }
}
```

Expected reply shape back into executor:

```json
{
  "type": "agent.step.status",
  "message_id": "019f...",
  "correlation_id": "019f...",
  "conv_id": "019f...",
  "payload": {
    "from": "agent.<id>",
    "to": "executor.runtime",
    "type": "step",
    "step": {
      "id": "019f...",
      "status": "success",
      "output_data": {}
    }
  }
}
```

Notes:

- The reply handler ignores non-terminal statuses such as `processing`, `running`, `in_progress`, and `interrupted`.
- Only terminal replies are used to complete the delegated step.

## 5. Gaps Between Code And Previous Docs

These older assumptions are not true in the current code:

- Orchestrator does not send a populated task list in `conversation.plan.created`.
- There is no common envelope field name across all producers.
- The current FE/backend code uses `chat:disable` and `chat:enable`, not `chat:disabled` and `chat:enabled`.
- The UI payload type is `form_tool`, not `tool_form`.
- Executor status replies use `payload.steps`, not `payload.step`.
- The business agent currently advances one executor step at a time.

## 6. Source Files

- `orchestrator/app/layer1_domain/entities/orchestrator/queue_event.py`
- `orchestrator/app/layer2_application/features/orchestrate_graph/use_cases/orchestrate_graph_use_case.py`
- `business-agent/app/layer1_domain/entities/queue_event.py`
- `business-agent/app/layer2_application/features/main_agent/use_cases/create_plan_usecase.py`
- `business-agent/app/layer2_application/features/main_agent/use_cases/review_plan_usecase.py`
- `executor-runtime-service/app/layer2_application/features/validate_step_batch/use_cases/validate_step_batch_use_case.py`
- `executor-runtime-service/app/layer2_application/queues/executor_queue_handler.py`
- `executor-runtime-service/app/layer4_frameworks/messaging/direct_step_status_publisher.py`
- `executor-runtime-service/app/layer4_frameworks/services/technical_agent_step_executor.py`

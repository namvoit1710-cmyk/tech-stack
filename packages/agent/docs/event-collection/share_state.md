# Shared State Shape

This document describes the shared state fields that the current `orchestrator` and `business-agent` code actually read and write.

It is not the older generic Agent SDK sketch.

## 1. Top-Level Shape

```python
Conversation(
    conv_id: str,
    user_id: str,
    shared_state=AgentSharedState(
        metadata=Metadata(...),
        context=GlobalContext(...),
        executions=list[Task],
    ),
)
```

Observed from current code:

- Orchestrator creates and updates the main conversation state.
- Business agent reads and mutates the same shared state.
- Executor runtime does not persist this shared state directly.

## 2. Metadata Fields In Active Use

Current code reads or writes these `Metadata` fields:

| Field | Used by | Meaning |
| --- | --- | --- |
| `conv_id` | Orchestrator, Business Agent | Conversation id |
| `main_conv_id` | Orchestrator, Business Agent | Empty or `None` for main conversation; populated for child conversations |
| `checkpoint_id` | Orchestrator, Business Agent | Current checkpoint id |
| `parent_checkpoint` | Orchestrator, Business Agent | Parent checkpoint for child flows |
| `last_event_id` | Stored, not central in reviewed code | Last processed event id |
| `correlation_id` | Orchestrator, Business Agent | Request correlation id |
| `current_task_id` | Orchestrator, Business Agent | Current task or child step being processed |
| `current_run_id` | Business Agent | Current workflow run id |
| `current_node_id` | Orchestrator, Business Agent | Current workflow node id |
| `current_agent` | Orchestrator, Business Agent | Current business agent id or execute target |
| `current_message_id` | Orchestrator, Business Agent | Current user message id |
| `status` | Business Agent | Conversation lifecycle status such as `IN_PROGRESS`, `COMPLETED`, `FAILED` |

## 3. Context Fields In Active Use

Current code uses `GlobalContext` mainly for conversation history and uploaded files.

### Main conversation path

Orchestrator creates:

```python
GlobalContext(
    conversation_context=[MessageHistory(...)],
)
```

Each new user message is appended to `context.conversation_context`.

### Child conversation path

Business agent creates child conversations with:

```python
GlobalContext(
    conversation_context=[],
    knowledge_base_id="",
)
```

Observed fields in use:

| Field | Meaning |
| --- | --- |
| `conversation_context` | Main request history or child conversation history |
| `files` | Uploaded files, created from FE `uploaded_file_ids` in orchestrator |
| `knowledge_base_id` | Present in some business-agent child conversation creation paths |

## 4. Task Shape In Active Use

The shared execution plan is stored in `shared_state.executions`.

Current code uses these task fields:

| Field | Used by | Meaning |
| --- | --- | --- |
| `id` or `task_id` | Orchestrator, Business Agent | Unique task id |
| `execute_by` or `agent` | Orchestrator, Business Agent | Execution target, usually `agent.<id>` or `tool.<id>` |
| `goal` or `name` | Orchestrator, Business Agent | Human-readable task goal |
| `status` | Orchestrator, Business Agent | `PENDING`, `PROCESSING`, `SUCCESS`, `ERROR`, and in some older paths `FAIL` |
| `parent_task_id` | Business Agent | Parent task relationship |
| `next_task_id` | Orchestrator, Business Agent | Sequential next task |
| `depend_on` | Orchestrator | Dependency ids used to find ready tasks |
| `workflow_id` | Orchestrator, Business Agent | Workflow id when task maps to workflow work |
| `agent_id` | Orchestrator, Business Agent | Registry business-agent id |
| `input_data` | Orchestrator, Business Agent, Executor Runtime | Structured input payload |
| `output_data` | Business Agent, Executor Runtime | Structured result payload |
| `error_detail` | Business Agent | Error detail when a task fails |

## 5. Main Conversation State Lifecycle

### 5.1 Conversation creation

Implemented in `orchestrator/.../orchestrate_use_case.py`.

On first message, orchestrator creates:

```python
Metadata(
    conv_id=<generated>,
    main_conv_id=None,
    checkpoint_id=<generated>,
    parent_checkpoint=None,
    last_event_id=None,
    correlation_id=<generated>,
    current_task_id=None,
    current_run_id=None,
    current_node_id=None,
    current_agent=None,
    current_message_id=<generated>,
)
```

and

```python
GlobalContext(
    conversation_context=[MessageHistory(...)]
)
```

### 5.2 Planning sync

Implemented in `orchestrator/.../orchestrate_graph_use_case.py`.

When the planner finishes:

- `shared_state.executions` is replaced with the new execution plan
- `metadata.current_task_id` is set to the first task
- `metadata.current_agent` is set from `task.agent_id` or `task.execute_by`

### 5.3 Business-agent step expansion

Implemented in `business-agent/.../create_plan_usecase.py`.

When the business agent expands a parent task into child steps:

- it writes child tasks back to `executions`
- it updates `metadata.current_task_id` to the first child step
- for workflow-start steps it may pre-generate `metadata.current_run_id`

### 5.4 Execution review

Implemented in `business-agent/.../review_plan_usecase.py`.

When `executor.step.status` arrives:

- `output_data` is copied from the executor event into the current task
- task status is updated to `PROCESSING`, `SUCCESS`, or `ERROR`
- next task dispatch or final summary depends on the updated state

## 6. Child Conversation State

Business agent creates child conversations for form/workspace/viewer flows.

Current creation pattern:

```python
Metadata(
    conv_id=<child-conv-id>,
    main_conv_id=<main-conv-id>,
    checkpoint_id=<child-conv-id>,
    parent_checkpoint=<main-checkpoint>,
    last_event_id=None,
    correlation_id=<child-conv-id>,
    branch="sub",
    current_agent=<main-current-agent>,
    status=IN_PROGRESS,
    current_task_id=None,
    current_run_id=<workflow-run-id>,
    current_node_id=<workflow-node-id>,
)
```

Purpose:

- FE receives `payload.sub_conv_id`
- FE opens or reuses a child conversation
- later `sub:user:chat` events route back through orchestrator and business agent

## 7. What The Shared State Is Not Used For

In the current implementation:

- Executor runtime does not update shared state directly.
- Orchestrator `conversation.plan.created` does not carry the full task list; the plan is read from shared state.
- FE timeline events are stored in event tables, not inside shared state.

## 8. Practical Minimum Contract

If another component needs to work with the current code, these fields are the minimum safe assumptions:

### Metadata

```json
{
  "conv_id": "conv-1",
  "main_conv_id": null,
  "checkpoint_id": "cp-1",
  "parent_checkpoint": null,
  "correlation_id": "corr-1",
  "current_task_id": "task-1",
  "current_run_id": "run-1",
  "current_node_id": "node-1",
  "current_agent": "agent-1",
  "current_message_id": "msg-1",
  "status": "IN_PROGRESS"
}
```

### Context

```json
{
  "conversation_context": [],
  "files": []
}
```

### Task

```json
{
  "id": "task-1",
  "execute_by": "tool.<uuid>",
  "goal": "Run step",
  "status": "PENDING",
  "parent_task_id": "",
  "next_task_id": "task-2",
  "depend_on": [],
  "input_data": {},
  "output_data": {},
  "error_detail": null
}
```

## 9. Source Files

- `orchestrator/app/layer2_application/features/orchestrate/use_cases/orchestrate_use_case.py`
- `orchestrator/app/layer2_application/features/orchestrate_graph/use_cases/orchestrate_graph_use_case.py`
- `business-agent/app/layer2_application/features/main_agent/use_cases/create_plan_usecase.py`
- `business-agent/app/layer2_application/features/main_agent/use_cases/review_plan_usecase.py`
- `business-agent/app/layer4_frameworks/repositories/hana_business_agent_shared_state_repository.py`

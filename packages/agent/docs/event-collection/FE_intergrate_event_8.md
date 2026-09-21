# FE Integration Event Contract

This document reflects the FE-facing event contract currently emitted by `business-agent` and `orchestrator`.

It replaces older examples that used values no longer present in code.

## 1. Event Envelope

Current FE events are stored and pushed as:

```json
{
  "event_id": "019f...",
  "event_type": "chat:thinking | chat:response | chat:disable | chat:enable | process:response",
  "correlation_id": "019f...",
  "conv_id": "019f...",
  "role": "assistant | user",
  "timestamp": "2026-06-25T10:00:00+00:00",
  "metadata": {
    "main_conv_id": "019f...",
    "sub_conv_ids": [],
    "uploaded_file_ids": [],
    "action_context": {
      "source_event_id": "019f...",
      "source_conv_id": "019f..."
    }
  },
  "payload": {
    "id": "019f...",
    "content": "Message shown to FE",
    "parent_id": "019f...",
    "message_id": "019f...",
    "type": "progressing_collapse | form_tool | text | button_group | open_workspace | view_space | open_viewer | token_info",
    "status": "processing | success | error",
    "sub_conv_id": "019f...",
    "text": ["Open Workspace"],
    "wf_info": {
      "node_id": "node-1",
      "task_id": "task-1",
      "run_id": "run-1"
    },
    "object_type_id": "BusinessPartner",
    "record_id": "1935",
    "change_request_id": "CR123"
  }
}
```

## 2. Current Event Types

| Value | Emitted by current code | Meaning |
| --- | --- | --- |
| `chat:thinking` | Yes | Non-final assistant progress text |
| `chat:response` | Yes | Final or summary assistant message |
| `chat:disable` | Yes | Disable chat input |
| `chat:enable` | Yes | Re-enable chat input |
| `process:response` | Yes | Structured workflow or task UI update |

Not currently emitted by these services:

- `process:complete`
- `workspace:reload`
- `chat:disabled`
- `chat:enabled`

## 3. Current Payload Types

| Value | Emitted by current code | Meaning |
| --- | --- | --- |
| `progressing_collapse` | Yes | Task progress item |
| `form_tool` | Yes | Workflow form entry |
| `text` | Yes | Plain process text |
| `button_group` | Yes | Action buttons |
| `open_workspace` | Yes | Workspace launch item |
| `view_space` | Yes | Open a record viewer by `record_id` |
| `open_viewer` | Yes | Open a viewer flow by `change_request_id` |
| `token_info` | Yes | Per-turn generation metrics line (SA-2079) — `payload.content` is a preformatted string (`Cooking… {duration} · {tokens} tokens` while `processing`, `✻ Cooked for {duration}, {tokens} tokens` once `success`); emitted on every LLM call in both services, plus once at turn finalization |

Not currently emitted:

- `tool_form`

## 4. Field Notes

### Root

| Field | Notes |
| --- | --- |
| `event_id` | Unique event id |
| `event_type` | See section 2 |
| `correlation_id` | Usually generated per emitted FE event |
| `conv_id` | Active conversation id used for FE routing |
| `role` | Usually `assistant`; orchestrator may persist `user` events for timeline history |
| `timestamp` | UTC timestamp |

### metadata

| Field | Notes |
| --- | --- |
| `main_conv_id` | Always present in current emitters |
| `sub_conv_ids` | Supported in schema; often empty in current emitters |
| `uploaded_file_ids` | Supported in schema |
| `action_context` | Present on actionable workflow UI events so FE can send the right follow-up action |

### payload

| Field | Notes |
| --- | --- |
| `id` | Stable UI node id; timeline projection uses this field |
| `content` | Main display text |
| `parent_id` | Parent task or parent node id when applicable |
| `message_id` | (SA-2079) Present on `token_info` events — the turn's assistant message id, stable across the whole turn and equal to the final `chat:response` content event's `payload.id`. This is the FE's grouping key: collapse the stream of `token_info` events for one message into a single line, and replay `Cooking…` -> `Cooked`. |
| `type` | See section 3 |
| `status` | Usually `processing`, `success`, or `error` |
| `sub_conv_id` | Used when the BE creates a child conversation for a form/workspace/viewer flow |
| `text` | Button labels or launcher labels such as `Open Workspace` |
| `wf_info` | Workflow identifiers needed for FE follow-up actions |
| `object_type_id` | Used by `view_space` |
| `record_id` | Used by `view_space` |
| `change_request_id` | Used by `open_viewer` |

## 5. Actual Event Patterns

### 5.1 Chat disable

```json
{
  "event_id": "019f...",
  "event_type": "chat:disable",
  "correlation_id": "019f...",
  "conv_id": "conv-1",
  "role": "assistant"
}
```

### 5.2 Chat thinking

```json
{
  "event_id": "019f...",
  "event_type": "chat:thinking",
  "correlation_id": "019f...",
  "conv_id": "conv-1",
  "role": "assistant",
  "metadata": {
    "main_conv_id": "conv-1",
    "sub_conv_ids": [],
    "uploaded_file_ids": []
  },
  "payload": {
    "id": "019f...",
    "content": "I'm taking a moment to organize the information and create a clear plan...",
    "parent_id": "task-1",
    "status": "success"
  }
}
```

Note:

- Current code often sets `chat:thinking` payload status to `success`, not `processing`.

### 5.3 Task progress

```json
{
  "event_id": "019f...",
  "event_type": "process:response",
  "correlation_id": "019f...",
  "conv_id": "conv-1",
  "metadata": {
    "main_conv_id": "conv-1",
    "sub_conv_ids": [],
    "uploaded_file_ids": []
  },
  "payload": {
    "id": "task-1",
    "parent_id": "",
    "type": "progressing_collapse",
    "content": "Generate report",
    "status": "processing"
  }
}
```

### 5.4 Workflow form

```json
{
  "event_id": "019f...",
  "event_type": "process:response",
  "correlation_id": "019f...",
  "conv_id": "conv-1",
  "metadata": {
    "main_conv_id": "conv-1",
    "sub_conv_ids": [],
    "uploaded_file_ids": [],
    "action_context": {
      "source_event_id": "019f...",
      "source_conv_id": "conv-1"
    }
  },
  "payload": {
    "id": "sub-conv-1",
    "parent_id": "task-1",
    "type": "form_tool",
    "content": "Please review and complete the form.",
    "status": "processing",
    "sub_conv_id": "sub-conv-1",
    "wf_info": {
      "node_id": "node-1",
      "task_id": "task-1",
      "run_id": "run-1"
    }
  }
}
```

### 5.5 Open workspace

```json
{
  "event_id": "019f...",
  "event_type": "process:response",
  "correlation_id": "019f...",
  "conv_id": "conv-1",
  "metadata": {
    "main_conv_id": "conv-1",
    "sub_conv_ids": [],
    "uploaded_file_ids": [],
    "action_context": {
      "source_event_id": "019f...",
      "source_conv_id": "conv-1"
    }
  },
  "payload": {
    "id": "sub-conv-2",
    "parent_id": "task-1",
    "type": "open_workspace",
    "content": "Open the workspace to continue.",
    "status": "processing",
    "sub_conv_id": "sub-conv-2",
    "text": ["Open Workspace"],
    "wf_info": {
      "node_id": "node-2",
      "task_id": "task-2",
      "run_id": "run-2"
    }
  }
}
```

### 5.6 Button group

```json
{
  "event_id": "019f...",
  "event_type": "process:response",
  "correlation_id": "019f...",
  "conv_id": "conv-1",
  "metadata": {
    "main_conv_id": "conv-1",
    "sub_conv_ids": [],
    "uploaded_file_ids": [],
    "action_context": {
      "source_event_id": "019f...",
      "source_conv_id": "conv-1"
    }
  },
  "payload": {
    "id": "019f...",
    "parent_id": "task-1",
    "type": "button_group",
    "content": "Please choose an action.",
    "status": "processing",
    "text": ["Yes", "No"],
    "wf_info": {
      "node_id": "node-3",
      "task_id": "task-3",
      "run_id": "run-3"
    }
  }
}
```

### 5.7 View space

```json
{
  "event_id": "019f...",
  "event_type": "process:response",
  "correlation_id": "019f...",
  "conv_id": "conv-1",
  "metadata": {
    "main_conv_id": "conv-1",
    "sub_conv_ids": [],
    "uploaded_file_ids": []
  },
  "payload": {
    "id": "019f...",
    "parent_id": "task-1",
    "type": "view_space",
    "content": "Record is ready for review.",
    "status": "success",
    "object_type_id": "BusinessPartner",
    "record_id": "1935"
  }
}
```

### 5.8 Open viewer

```json
{
  "event_id": "019f...",
  "event_type": "process:response",
  "correlation_id": "019f...",
  "conv_id": "conv-1",
  "metadata": {
    "main_conv_id": "conv-1",
    "sub_conv_ids": [],
    "uploaded_file_ids": [],
    "action_context": {
      "source_event_id": "019f...",
      "source_conv_id": "conv-1"
    }
  },
  "payload": {
    "id": "sub-conv-3",
    "parent_id": "task-1",
    "type": "open_viewer",
    "content": "Open the approval viewer.",
    "status": "processing",
    "sub_conv_id": "sub-conv-3",
    "text": ["Open Viewer"],
    "wf_info": {
      "node_id": "node-4",
      "task_id": "task-4",
      "run_id": "run-4"
    },
    "change_request_id": "CR123"
  }
}
```

### 5.9 Final summary

```json
{
  "event_id": "019f...",
  "event_type": "chat:response",
  "correlation_id": "019f...",
  "conv_id": "conv-1",
  "metadata": {
    "main_conv_id": "conv-1",
    "sub_conv_ids": [],
    "uploaded_file_ids": []
  },
  "payload": {
    "id": "019f-msg-1",
    "content": "Workflow completed successfully.",
    "status": "success"
  }
}
```

Note (SA-2079): when a turn emitted `token_info` events, the final content event's `payload.id`
is the same value as those events' `payload.message_id` (here `019f-msg-1`), so the FE can tie
the settled metrics line to this message.

### 5.10 Token info

```json
{
  "event_id": "019f-a1...",
  "event_type": "chat:response",
  "correlation_id": "019f-a1...",
  "conv_id": "conv-1",
  "metadata": {
    "main_conv_id": "conv-1",
    "sub_conv_ids": [],
    "uploaded_file_ids": []
  },
  "payload": {
    "id": "019f-a1...",
    "message_id": "019f-msg-1",
    "type": "token_info",
    "status": "processing",
    "content": "Cooking… 3.2s · 1,682 tokens"
  }
}
```

Followed, once at turn finalization, by the settled line (same `message_id`, a fresh `id`):

```json
{
  "event_id": "019f-a2...",
  "event_type": "chat:response",
  "correlation_id": "019f-a2...",
  "conv_id": "conv-1",
  "metadata": {
    "main_conv_id": "conv-1",
    "sub_conv_ids": [],
    "uploaded_file_ids": []
  },
  "payload": {
    "id": "019f-a2...",
    "message_id": "019f-msg-1",
    "type": "token_info",
    "status": "success",
    "content": "✻ Cooked for 1m 57s, 1,682 tokens"
  }
}
```

Notes:

- Emitted once per LLM call in both `orchestrator` and `business-agent` (`status: processing`,
  cumulative `total_tokens` + elapsed time for the turn so far), plus exactly one
  `status: success` event at turn finalization, right before the final `chat:response` content
  event (which reuses the same `message_id` as its own `payload.id`).
- Each `token_info` event has a fresh `payload.id` and persists like any other event — no id
  sharing, no clobbering of the assistant message.
- `payload.message_id` is the FE's grouping key (SA-2080, out of scope here): collapse the stream
  of `token_info` events for one message into a single line, and replay `Cooking…` -> `Cooked`.
- Token counts come from the LLM provider's usage when available; when absent, `business-agent`
  estimates via `tiktoken` and `orchestrator` omits the token count but still reports elapsed time.
- Reopen filtering (keep only `status: success` per `message_id`) is a separate future ticket.

## 6. FE -> BE Follow-up Action Contract

Current follow-up actions are handled by `orchestrator/app/layer2_application/features/orchestrate/use_cases/agent_action_use_case.py`.

FE should send:

```json
{
  "wf_info": {
    "run_id": "run-1",
    "node_id": "node-1",
    "task_id": "task-1"
  },
  "action": "save",
  "button_label": "Approve",
  "form_data": {
    "values": {
      "fieldA": "value"
    }
  },
  "metadata": {
    "action_context": {
      "source_event_id": "019f...",
      "source_conv_id": "conv-1"
    }
  },
  "source": "ui"
}
```

Notes:

- `wf_info` is required for the current fire-and-forget action path.
- `metadata.action_context` is how orchestrator finds the source UI event and writes a success or error update back into the timeline.
- For button actions, orchestrator can collapse the `text` list to the selected label in the follow-up event.

## 7. Timeline And Reopen Behavior

Current persistence behavior:

- Every FE event is stored in `AIW_AGENT_EVENT`.
- Timeline projection uses `payload.id`.
- `chat:thinking` is stored but skipped from timeline projection.
- The last `chat:disable` or `chat:enable` event is fetched separately for reopen state.

## 8. Source Files

- `business-agent/app/layer1_domain/entities/ui_event.py`
- `business-agent/app/layer1_domain/entities/turn_meter.py`
- `business-agent/app/layer4_frameworks/messaging/push_gateway_notifier.py`
- `business-agent/app/layer4_frameworks/messaging/push_gateway_usage_recorder.py`
- `orchestrator/app/layer1_domain/entities/orchestrator/ui_event.py`
- `orchestrator/app/layer1_domain/entities/orchestrator/turn_meter.py`
- `orchestrator/app/layer4_frameworks/messaging/push_gateway_notifier.py`
- `orchestrator/app/layer4_frameworks/ai/instrumented_llm_provider.py`
- `orchestrator/app/layer2_application/features/orchestrate/use_cases/agent_action_use_case.py`
- `orchestrator/app/layer4_frameworks/repositories/agent_event_repository.py`

# Orchestrator Conversations and Orchestrate API Guide

This guide documents the REST endpoints currently exposed by the orchestrator service for conversation lookup, message append, orchestration dispatch, and agent-related helper APIs.

## Base URL

Examples in this document assume the service is running at:

```text
http://localhost:8005
```

Available route groups:

- `/conversations`
- `/orchestrate`

Health check:

- `GET /health`

Example response:

```json
{
  "status": "ok"
}
```

## Conversations API

### Get Conversation

`GET /conversations/{conversation_id}`

Returns one conversation including stored messages and active agents.

Path parameters:

- `conversation_id`: conversation identifier

Example:

```bash
curl "http://localhost:8005/conversations/conv-001"
```

Example response when found:

```json
{
  "conversation_id": "conv-001",
  "user_id": "u123",
  "messages": [
    {
      "role": "user",
      "content": "hello",
      "timestamp": "2026-04-08T10:16:00.000000+00:00"
    }
  ],
  "active_agents": [
    "workflow-agent"
  ],
  "created_at": "2026-04-08T10:15:30.000000+00:00",
  "run_id": "run-123",
  "workflow_id": "wf-456",
  "workflow_name": "Vendor Onboarding",
  "workflow_status": "in_progress",
  "error": null
}
```

Example response when not found:

```json
{
  "conversation_id": "conv-001",
  "user_id": "",
  "messages": [],
  "active_agents": [],
  "created_at": "",
  "run_id": "",
  "workflow_id": "",
  "workflow_name": "",
  "workflow_status": "",
  "error": "Conversation not found"
}
```

Response fields also include:

- `run_id`: workflow run identifier when conversation context exists
- `workflow_id`: workflow identifier when conversation context exists
- `workflow_name`: workflow name when conversation context exists
- `workflow_status`: workflow status when conversation context exists

### List Conversations

`GET /conversations`

Returns a paginated list of conversations.

Authentication:

- No bearer token is required for this endpoint.

Query parameters:

- `user_id`: optional string, default is empty
- `tenant_id`: optional string, default is empty
- `page_index`: optional integer, minimum `1`, default is `1`
- `page_size`: optional integer, minimum `1`, default is `10`

Example:

```bash
curl "http://localhost:8005/conversations?user_id=u123&tenant_id=default&page_index=1&page_size=10"
```

Example response:

```json
{
  "conversations": [
    {
      "conversation_id": "conv-002",
      "user_id": "u123",
      "message_count": 4,
      "created_at": "2026-04-08T11:00:00.000000+00:00",
      "run_id": "run-456",
      "workflow_id": "wf-456",
      "workflow_name": "Vendor Onboarding",
      "workflow_status": "completed"
    },
    {
      "conversation_id": "conv-001",
      "user_id": "u123",
      "message_count": 2,
      "created_at": "2026-04-08T10:15:30.000000+00:00",
      "run_id": "run-123",
      "workflow_id": "wf-123",
      "workflow_name": "Workflow Draft",
      "workflow_status": "in_progress"
    }
  ],
  "page_index": 1,
  "page_size": 10,
  "total_items": 2,
  "total_pages": 1
}
```

Notes:

- Pagination is page-based.
- `message_count` is computed from the stored conversation messages.
- Each conversation summary may include `run_id`, `workflow_id`, `workflow_name`, and `workflow_status` when conversation context exists.
- The endpoint accepts `tenant_id`, but filtering behavior depends on the backing repository implementation.

## Orchestrate API

### Run Orchestration Asynchronously

`POST /orchestrate/async`

Accepts an orchestration request and returns immediately with HTTP `202`.

Authentication:

- Requires `Authorization: Bearer <jwt-token>`.

Request body:

```json
{
  "message": "Create a workflow for vendor onboarding",
  "conv_id": "conv-001",
  "parameters": {
    "uploaded_file_ids": ["file_id_123"],
    "run_id": "run-123"
  }
}
```

Fields:

- `message`: required string
- `conv_id`: optional string. When omitted, the use case creates a new conversation id.
- `parameters`: optional object passed through to the orchestration pipeline.
- `parameters.uploaded_file_ids`: optional array of strings.
- `parameters.run_id`: optional string.

Response fields:

- `status`: async request state. The endpoint returns `started` when the orchestration request has been queued.
- `message`: the submitted user message.
- `main_conv_id`: main conversation id associated with the orchestration request.
- `sub_conv_ids`: currently returned as a list and empty for the controller path shown here.
- `uploaded_file_ids`: echoes `parameters.uploaded_file_ids` from the request.
- `workflow_id`: workflow identifier if already resolved; otherwise an empty string.

Example:

```bash
curl -X POST "http://localhost:8005/orchestrate/async" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <jwt-token>" \
  -d "{\"message\":\"Create a workflow for vendor onboarding\",\"conv_id\":\"conv-001\",\"parameters\":{\"uploaded_file_ids\":[\"file_id_123\"],\"run_id\":\"run-123\"}}"
```

Example success response:

```json
{
  "status": "started",
  "message": "Create a workflow for vendor onboarding",
  "main_conv_id": "conv-001",
  "sub_conv_ids": [],
  "uploaded_file_ids": ["file_id_123"],
  "workflow_id": ""
}
```

### Submit Agent Action

`POST /orchestrate/agent-action`

Queues a user action for an existing workflow node action and returns immediately with HTTP `202`.

Authentication:

- Requires `Authorization: Bearer <jwt-token>`.

Request body:

```json
{
  "wf_info": {
    "run_id": "run-42",
    "node_id": "node-9",
    "task_id": "task-7"
  },
  "action": "button",
  "values": {
    "field": "value"
  },
  "button_label": "Approve",
  "form_data": {
    "section": "details"
  },
  "metadata": {
    "source": "workspace"
  }
}
```

Fields:

- `wf_info.run_id`: required string when targeting a workflow node action.
- `wf_info.node_id`: required string when targeting a workflow node action.
- `wf_info.task_id`: optional string.
- `action`: optional string, defaults to `save`.
- `values`: optional object.
- `button_label`: optional string.
- `form_data`: optional object.
- `metadata`: optional object.

Behavior:

- The endpoint publishes a `USER_ACTION` queue event to the configured agent request topic.
- The bearer token is required for access but is not forwarded into the use case input.

Example:

```bash
curl -X POST "http://localhost:8005/orchestrate/agent-action" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <jwt-token>" \
  -d "{\"wf_info\":{\"run_id\":\"run-42\",\"node_id\":\"node-9\",\"task_id\":\"task-7\"},\"action\":\"button\",\"values\":{\"field\":\"value\"},\"button_label\":\"Approve\",\"form_data\":{\"section\":\"details\"},\"metadata\":{\"source\":\"workspace\"}}"
```

Example response:

```json
{
  "run_id": "run-42",
  "node_id": "node-9",
  "task_id": "task-7"
}
```

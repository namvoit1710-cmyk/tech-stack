# Workers Reference

Workers execute [`TASK`](/nodes/task) nodes. Each registers under a `worker_type`; set that value on the `TASK` node and fill in the worker's input fields. See [Workers setup](/guide/workers-setup) to run them.

| Worker | `worker_type` | Default port |
|---|---|---|
| [HTTP Request](#http-request-worker) | `http_request` | `35000` |
| [Agent](#agent-worker) | `agent` | *(SDK default)* |
| [Mapping Data](#mapping-data-worker) | `mapping_data` | *(SDK default)* |
| [Wait](#wait-worker) | `wait` | `35000` |
| [Database Connection](#database-connection-worker) | `database_connection` | `37000` |
| [MS Teams](#ms-teams-worker) | `ms_teams` | `38000` |
| [Gateway](#gateway-worker) | `gateway-worker` | `35001` |

---

## http-request-worker

**`worker_type: http_request`** — general-purpose HTTP/REST client with auth, headers, query params, and multiple body types.

### Input

| Field | Type | Notes |
|---|---|---|
| `method` | select | **Required.** `GET` / `POST` / `PUT` / `PATCH` / `DELETE` / `HEAD` / `OPTIONS`. |
| `url` | string | **Required.** Must be a valid URL. |
| `headers` | array | Items of `{ header_name, header_value }`. |
| `query_params` | array | Items of `{ param_name, param_value }`. |
| `body_type` | select | `none` / `raw_json` / `raw_text` / `structured` / `form_urlencoded` / `file`. |
| `body` | string | Raw body (textarea). |
| `body_fields` | array | For structured/form bodies: `{ field_name, field_type, field_value }`. |
| `body_file` | upload | File body. |
| `timeout_seconds` | number | `1`–`300`, default `30`. |
| `auth_type` | select | `none` / `bearer` / `basic` / `api_key`. |
| `auth_token` | string | Bearer token (password field). |
| `auth_username` | string | Basic auth username. |
| `auth_password` | string | Basic auth password (password field). |
| `api_key_header` | string | API-key header name, default `X-API-Key`. |
| `follow_redirects` | boolean | Default `true`. |
| `verify_ssl` | boolean | Default `true`. |

### Output

| Field | Type | Notes |
|---|---|---|
| `status_code` | number | HTTP status. |
| `response_headers` | object | Response headers. |
| `response_body` | string/object | Parsed/raw response body. |
| `content_type` | string | Response content type. |
| `elapsed_ms` | number | Round-trip time. |
| `is_success` | boolean | Whether the status indicates success. |
| `final_url` | string | URL after redirects. |
| `resolved_body` | string | The request body that was sent, **truncated to the first 4 KB**. Not the whole body: a body bound to a file_ref resolves to megabytes, and echoing it made the task result too large for the 1 MiB broker cap (`RESULT_TOO_LARGE`). |
| `resolved_body_bytes` | number | Full size of the sent body. **`0` means the request went out with no body** — the signal that a claim-checked input lost its array fields. |
| `resolved_body_sha256` | string | sha256 of the full sent body, so it can still be proven without being carried. |
| `resolved_body_truncated` | boolean | Whether `resolved_body` was cut. |

---

## agent-worker

**`worker_type: agent`** — LLM inference. Provider: **OpenAI**.

### Input

| Field | Type | Notes |
|---|---|---|
| `provider` | select | **Required.** `openai`. |
| `model` | string | **Required.** e.g. `gpt-4o`. |
| `api_key` | string | **Required** (password field). |
| `system_prompt` | string | Optional system prompt (textarea). |
| `user_prompt` | string | **Required.** Supports `{{variable}}` substitution (textarea). |
| `prompt_variables` | array | `{ variable_name, variable_value }` pairs. |
| `conversation_history` | array | `{ role (user/assistant), content }`. |
| `temperature` | number | `0.0`–`2.0`, default `0.7`. |
| `max_tokens` | number | `1`–`128000`, default `1024`. |
| `top_p` | number | `0.0`–`1.0`, default `1.0`. |
| `response_format` | select | `text` / `json`. |

### Output

| Field | Type | Notes |
|---|---|---|
| `response_content` | string | The model's reply. |
| `model_used` | string | Model that served the request. |
| `finish_reason` | string | Why generation stopped. |
| `prompt_tokens` | number | Input token count. |
| `completion_tokens` | number | Output token count. |
| `total_tokens` | number | Total tokens. |

::: tip
Pair this with a [`PROMPT`](/nodes/transform#prompt) node: build the prompt text there, then feed `{{$prompt-node.output.content}}` into `user_prompt`.
:::

---

## mapping-data-worker

**`worker_type: mapping_data`** — transform a source structure into a target structure, including array iteration and per-field mapping.

### Input

| Field | Type | Notes |
|---|---|---|
| `source_data` | object | **Required.** The data to transform. |
| `mapping_rules` | array | Field-to-field mapping rules (below). |
| `strict_mode` | boolean | Default `false`. Fail on missing source paths when `true`. |
| `array_mappings` | array | Array-transformation rules (below). |

Each `mapping_rules` item:

| Sub-field | Type | Notes |
|---|---|---|
| `source_path` | string | **Required** (except `constant` mode). Dotted path with `[i]` indices, e.g. `response_body.data[0].name`. |
| `target_field` | string | **Required.** Key to write in the output. |
| `field_type` | select | `string` / `number` / `boolean` / `object` / `array`. Coerces the value. |
| `default_value` | string | Fallback when the source is missing/null. |
| `mapping_mode` | select | `direct` (copy from `source_path`) / `constant` (use `default_value`). |

Each `array_mappings` item:

| Sub-field | Type | Notes |
|---|---|---|
| `source_array_path` | string | **Required.** Path to the source array. |
| `target_field` | string | **Required.** Where to write the result. |
| `output_mode` | select | `objects` / `values`. |
| `pluck_field` | string | Field to pluck when `output_mode = values`. |
| `item_mappings` | array | Per-item field maps: `{ source_path, target_field, field_type, default_value, mapping_mode }`. |

### Output

| Field | Type | Notes |
|---|---|---|
| `mapped_data` | object | The transformed result. |
| `applied_rules_count` | number | How many rules applied. |
| `skipped_rules` | array | Rules that were skipped. |
| `errors` | array | Per-rule errors. |

---

## wait-worker

**`worker_type: wait`** — pause for a duration, until a datetime, or for manual approval.

### Input

| Field | Type | Notes |
|---|---|---|
| `wait_type` | select | **Required.** `fixed_duration` / `until_datetime` / `manual_approval`. |
| `duration_seconds` | number | `0`–`86400`. Used for `fixed_duration`. |
| `until_datetime` | string | ISO 8601. Used for `until_datetime`. |
| `timeout_seconds` | number | `0`–`604800`, default `3600`. |
| `reason` | string | Max 500 chars. |

### Output

| Field | Type | Notes |
|---|---|---|
| `wait_status` | select | `completed` / `timed_out` / `cancelled` / `error`. |
| `wait_type` | select | The wait type used. |
| `started_at` | string | Start timestamp. |
| `completed_at` | string | End timestamp. |
| `actual_duration_seconds` | number | Real elapsed time. |
| `reason` | string | Echo of the reason. |
| `message` | string | Status message. |

---

## database-connection-worker

**`worker_type: database_connection`** — run SQL against PostgreSQL, MySQL, or SAP HANA.

### Input

| Field | Type | Notes |
|---|---|---|
| `db_type` | select | **Required.** `postgresql` / `mysql` / `hana`. |
| `host` | string | **Required.** |
| `port` | number | `1`–`65535`. |
| `database` | string | **Required.** |
| `username` | string | **Required.** |
| `password` | string | **Required** (password field). |
| `operation_type` | select | **Required.** `select` / `insert` / `update` / `delete`. |
| `raw_sql` | string | **Required.** The SQL to execute (textarea). |
| `max_rows` | number | `1`–`10000`, default `1000`. SELECT only. |

### Output

Returns the query result set (for `select`) or affected-row information (for write operations), plus execution metadata.

::: warning Credentials
Database credentials are part of the task input. Prefer sourcing them from run-scoped variables or a secret store rather than hard-coding them in the workflow definition.
:::

---

## ms-teams-worker

**`worker_type: ms_teams`** — send messages and Adaptive Cards to Microsoft Teams via an incoming webhook.

### Input

| Field | Type | Notes |
|---|---|---|
| `webhook_url` | string | **Required** (password field). The Teams incoming webhook URL. |
| `operation_type` | select | **Required.** `send_message` / `send_adaptive_card`. |
| `message_title` | string | For `send_message`. |
| `message_content` | string | For `send_message` (textarea). |
| `card_title` | string | For `send_adaptive_card` (required). |
| `card_body` | string | For `send_adaptive_card` (required, textarea). |
| `card_actions` | array | Action buttons for adaptive cards. |

### Output

Returns the delivery status of the message/card.

---

## gateway-worker

**`worker_type: gateway-worker`** — a passthrough executor for gateway-function calls. The real external invocation is resolved in the control plane (via function-call markers in the `TASK` input); this worker echoes the already-resolved response back as the node output.

### Configuration (process env)

| Variable | Default | Notes |
|---|---|---|
| `CONTROL_PLANE_URL` | `http://localhost:8001` | CP endpoint for resolve calls. |
| `RESOLVE_CACHE_TTL_S` | `60.0` | Resolve-cache TTL in seconds. |
| `EXTERNAL_ALLOWLIST_PATH` | `config/external.yaml` | Allowed external hosts. |
| `INVOKE_ALLOWED_SCHEMES` | `https` | Permitted URL schemes. |
| `UPSTREAM_DEFAULT_TIMEOUT_S` | `30.0` | Upstream call timeout. |
| `UPSTREAM_MAX_RESPONSE_BYTES` | `5 MiB` | Max upstream response size. |

### Input / Output

Passthrough — the worker echoes the resolved task input as its output without transformation.

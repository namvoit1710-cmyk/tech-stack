# Agentic Coder Worker (`agentic_coder`)

The LLM half of the ticket-automation pipeline (Jira → Claude → PR), expressed as
an ai-workflow **worker node**. Given a ticket (summary + description + acceptance
criteria) and optional repo context / coding rules, one call to this worker does
**one bounded pass**:

- decides whether the ticket is **clear enough** (`enough` / `questions`) — the
  understanding gate; and
- if clear, returns the smallest implementation as a **unified-diff `patch`**, the
  **`files_changed`** list, and a **self-assessed `score`** (0–100), plus token
  counts and an approximate **`cost_usd`**.

Iteration ("keep trying until `score >= 100`") is the workflow **`LOOP`** node's job —
this worker owns a single pass so the graph stays inspectable.

## Layout

Standard single-type worker (mirrors `http-request-worker` / `agent-worker`):

```
agentic-coder-worker/
├── main.py                          # run_worker(...) — registers worker_type=agentic_coder
├── requirements.txt                 # -e ../worker-sdk + httpx
├── app/layer4_frameworks/config.py  # Settings: WORKER_TYPE=agentic_coder, SERVER_PORT=36000
├── app/layer2_application/features/execute_task/use_cases/execute_task_usecase.py
│                                    # ExecuteTaskUseCase + input/output schema + Anthropic call
└── examples/
    ├── ticket-to-patch.workflow.json  # TRIGGER → agentic_coder → CONDITION(enough) → OUTPUT
    └── run-input.example.json         # sample run input
```

## Inputs

| Field | Notes |
|---|---|
| `provider` | `anthropic` (only option today) |
| `model` | default `claude-opus-4-8` |
| `api_key` | fallback for local testing; a **vault credential** (`credential_ref`) is preferred and wins |
| `ticket_summary`, `ticket_description` | **required** |
| `acceptance_criteria`, `repo_context`, `rulebook` | optional context |
| `max_tokens` (4096), `temperature` (0.2) | model bounds |

## Outputs

`enough` (bool), `questions` (array), `summary`, `patch`, `files_changed` (array),
`score` (0–100), `input_tokens`, `output_tokens`, `total_tokens`, `cost_usd`,
`model_used`, `finish_reason`, `elapsed_ms`, `raw_response`.

## Run it

```bash
# from the workspace root, with the shared .venv active + worker-sdk installed
export REGISTRY_URL=http://localhost:8004   # the Worker Executor (NOT 8000)
export SERVER_PORT=36000
cd workflow/worker/agentic-coder-worker
pip install -r requirements.txt
python main.py            # -> "Mode: SERVER on localhost:36000"
```

Confirm registration: `curl http://localhost:8004/api/v1/workers` (look for `agentic_coder`).

See **`examples/ticket-to-patch.workflow.json`** for a runnable workflow and the
Desktop test guide for the browser walkthrough.

> Cost is **approximate** (list-price estimate in the use-case `_PRICING` map), for
> reporting only — not billing. Auth never reads a key from disk/env implicitly.

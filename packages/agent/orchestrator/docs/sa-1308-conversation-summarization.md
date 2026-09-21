# SA-1308 — Orchestrator: summarize the conversation

## Problem

The Orchestrator flow is: receive message → LLM intent classification → query the Agent
Registry → build a task plan → dispatch to a Business Agent
(`orchestrate_graph_use_case._classify_and_build_plan`). There is no agent whose job is to
"summarize the whole conversation", so a summarize request produces an **empty plan** and the
user hits `UnSupportedError` (`ORCH_011`, "…not currently supported…"). Worse, even if such a
request were force-routed to a Business Agent, the planner only ever sees the *current* task
goal + the agent's business intent + RAG candidates — it **never reads the prior turns**.

## Options considered

**Option A — a dedicated "summarize" Business Agent.**
A new agent registered in the Agent Hub, discovered by the planner, invoked over Kafka.
- ➖ Heavyweight: registry entry, executor wiring, Kafka topics, deploy — for a capability no
  master-data *workflow* backs.
- ➖ Still needs the conversation history plumbed into agent invocation (the very gap we're
  fixing), so it doesn't actually avoid the hard part.
- ➖ The planner would have to learn to route "summarize" to it — more LLM-routing surface.

**Option B — handle summarization in the Orchestrator (CHOSEN).**
Detect the summarize-conversation intent early and answer it directly.
- ➕ "Summarize this conversation" is a **meta-operation over the conversation itself**, not a
  governance workflow — it does not belong to a Business Agent conceptually.
- ➕ The Orchestrator already owns the message history
  (`agent_event_repo.get_events_by_conversation_id`) and an `llm_provider`. Everything needed
  is already in-hand.
- ➕ Sidesteps the RAG/planner limitation entirely: summarization reads the **full transcript**,
  not RAG candidates.
- ➕ Minimal, self-contained, unit-testable; no new deployable, no new topic.

## Design (Option B)

New pure-domain pieces (`layer1_domain`):
- `entities/classifier/summarize_intent.py` — `SummarizeIntentDetector`, a conservative
  heuristic (EN + VI). A summarize cue aimed at a concrete asset ("summarize this uploaded
  file") is **not** a conversation summary and still routes to a Business Agent.
- `prompts/build_summary_prompt.py` — the summary system/user prompt. The summary must cover
  the ticket's three asks: content, what the system did, recommended next steps.
- `entities/planner/conversation_summarizer.py` — `ConversationSummarizer` +
  `ConversationSummaryResponse` (`summary`, `what_the_system_did`, `recommended_next_steps`).
  Builds the transcript, calls `llm_provider.complete_with_format`, renders markdown.

Wiring (`layer2_application`):
- `orchestrate_graph_use_case.py`: in `_trigger_graph_for_main_conversation`, after the
  input-guard and the `wf_info` fast-path and **before** `_classify_and_build_plan`, check
  `SummarizeIntentDetector.is_summarize_request(...)`. If true, call
  `_handle_summarize_request`, which loads turns via `_load_conversation_turns` (excludes
  interim "thinking" events and the summarize request itself), summarizes, and emits a
  `chat:response`. No task plan, no `UnSupportedError`.

Dependencies keep pointing inward (frameworks → adapters → application → domain); the new L1
code imports only L1 interfaces/prompts. Verified by `tests/unit/test_architecture_boundaries.py`.

## Tests

- `tests/unit/layer1_domain/test_summarize_intent_detector.py` — recognizes conversation
  summaries (EN + VI), rejects asset-targeted summaries and normal requests.
- `tests/unit/layer1_domain/test_conversation_summarizer.py` — transcript building, empty
  history → `None`, LLM called with the right model, markdown rendering.
- `tests/unit/layer2_application/test_summarize_request_use_case.py` — summarize short-circuits
  the planner (no "Not supported"), reads history (thinking events excluded), emits a summary;
  a normal request still plans.

Full suite: **200 passed, 2 skipped**. `ruff` clean.

## Out of scope / follow-ups

- Persisting the generated summary (there is already a `conversation_summary_repo` used for
  titles; storing full summaries can reuse it).
- Streaming the summary token-by-token instead of a single `chat:response`.
- Localizing the rendered section headers (currently English) to the user's locale.

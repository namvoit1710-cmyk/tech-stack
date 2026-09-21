# Business Agent Builder

**SA-1431 · [Orchestration] Create business agent via chat.**

A backend agent (built on `agent-sdk`) that turns a **natural-language prompt** into a fully
populated **business-agent configuration**, so an Agent Hub user can create an agent by chatting
instead of manually filling every field. The Agent Hub "Create via chat" button calls this service.

## What it does

1. Receives a prompt (from the Agent Hub chat box) in graph state as `prompt`.
2. The `draft_business_agent` node runs `DraftBusinessAgentUseCase`:
   - **With an LLM** (`LLM_MODEL` set): asks the model to emit a JSON config, parsed defensively
     (tolerates prose / ```json fences), then completes any missing field from the heuristic.
   - **Without an LLM**: a deterministic keyword heuristic drafts sensible fields — so the service
     (and the test suite) run fully offline.
3. Returns a `BusinessAgentDraft` whose `to_register_payload()` matches the agent-registry
   `RegisterAgentRequest` shape and can be POSTed to `/api/v1/agents/register` (or used to pre-fill
   the Agent Hub builder form for human review before saving).

## Structure

```text
business-agent-builder/
├── app/
│   ├── layer1_domain/entities/
│   │   ├── business_agent_draft.py   # BusinessAgentDraft (validation + to_register_payload)
│   │   └── state.py                  # MainState(prompt, draft)
│   ├── layer2_application/
│   │   ├── enums/graph_node_enum.py  # GraphNode.DRAFT = "draft_business_agent"
│   │   ├── features/main_agent/use_cases/
│   │   │   └── draft_business_agent_usecase.py
│   │   ├── graphs/
│   │   │   ├── nodes/draft_node.py
│   │   │   └── main_flow.py          # DRAFT -> __end__
│   │   ├── prompts/prompt.py         # DRAFT_SYSTEM_PROMPT
│   │   ├── queues/queue_handler.py   # no-op (CONSUMER-mode stub)
│   │   └── tools/suggest_capabilities_tool.py
│   └── layer4_frameworks/
│       ├── config/app_config.py      # SERVER_PORT=36011, AGENT_TYPE=business-agent-builder
│       ├── dependency/ioc_container.py
│       └── graph/graph_builder.py
├── tests/ (unit + integration)
├── bootstrap.py · main.py · env.example · requirements.txt
```

## Business-agent config (drafted fields)

Mirrors `agent-registry` `RegisterAgentRequest`: `name`, `description`, `kind` (`business`),
`status` (`active`), `config_type` (`custom`), `business` (system prompt / context), plus optional
`tools`, `workflows`, `agents`, `knowledge_base`, `model`, `provider`, `temperature`, `capabilities`
(carried in `metadata`), `is_published`, `version`.

## Run & test

```bash
# tests (offline; LLM disabled)
python3 -m pytest -q --cov=app --cov-report=term-missing   # 85% coverage gate

# serve
python3 main.py     # uvicorn on SERVER_PORT (36011)
```

### Example call

```bash
curl -X POST http://localhost:36011/api/v1/execute \
  -H "Content-Type: application/json" \
  -d '{"message":"create an agent","parameters":{"prompt":"answer customer support email and draft replies"}}'
```

Returns a drafted business-agent config (`agent_result.payload`) ready for `/api/v1/agents/register`.

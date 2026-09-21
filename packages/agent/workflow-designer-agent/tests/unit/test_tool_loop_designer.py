from __future__ import annotations

from langchain_core.messages import AIMessageChunk

from app.layer1_domain.entities.designer_run_result import DesignerRunResult
from app.layer1_domain.entities.workflow_design_state import ConversationTurn
from app.layer4_frameworks.designer.tool_loop_designer import ToolLoopWorkflowDesigner


class FakeAI:
    """Minimal stand-in for an AIMessage with tool calls."""

    def __init__(self, content="", tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class ScriptedClient:
    """Returns pre-scripted AIMessages, one per ainvoke; records the tools it was bound."""

    def __init__(self, script) -> None:
        self._script = script
        self._i = 0
        self.bound = None

    def bind_tools(self, tools):
        self.bound = tools
        return self

    async def ainvoke(self, messages):
        msg = self._script[self._i]
        self._i += 1
        return msg

    async def astream(self, messages):
        # Yield the scripted message as a single chunk (content + tool_calls intact),
        # exercising the designer's token-streaming path.
        msg = self._script[self._i]
        self._i += 1
        yield msg


class FakeLLM:
    def __init__(self, client) -> None:
        self._client = client

    def get_chat_client(self):
        return self._client


class FakeTool:
    def __init__(self, name, result) -> None:
        self.name = name
        self._result = result
        self.calls: list[dict] = []

    async def ainvoke(self, args):
        self.calls.append(args)
        return self._result


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event, data) -> None:
        self.events.append((event, data))


def _designer(script, tools):
    return ToolLoopWorkflowDesigner(FakeLLM(ScriptedClient(script)), tools)


def _call(name, args, cid):
    return {"name": name, "args": args, "id": cid}


async def test_loop_discovers_and_hands_back_a_candidate() -> None:
    list_tool = FakeTool("list_node_types", {"node_types": [{"id": "builtin:prompt"}]})
    create_tool = FakeTool("create_workflow", {
        "status": "ready", "definition": {"name": "wf", "nodes": [], "edges": []},
    })
    script = [
        FakeAI("First I'll look at the palette.", [_call("list_node_types", {}, "1")]),
        FakeAI("Now I'll build it.", [_call("create_workflow", {"name": "wf"}, "2")]),
        FakeAI("Done — here is your workflow."),
    ]
    sink = RecordingSink()
    result = await _designer(script, [list_tool, create_tool]).run("build a flow", [], sink)

    assert result.status == DesignerRunResult.CANDIDATE
    assert result.definition == {"name": "wf", "nodes": [], "edges": []}
    assert result.workflow_id == ""
    kinds = [e for e, _ in sink.events]
    assert "thinking" in kinds and "tool_call" in kinds and "tool_result" in kinds


async def test_loop_returns_clarification() -> None:
    clarify_tool = FakeTool("ask_for_clarify",
                            {"status": "needs_clarification", "question": "Which file?"})
    script = [FakeAI("I need more info.", [_call("ask_for_clarify", {"question": "Which file?"}, "1")])]
    result = await _designer(script, [clarify_tool]).run("do stuff", [], None)
    assert result.status == DesignerRunResult.NEEDS_CLARIFICATION
    assert result.clarify_question == "Which file?"


async def test_run_workflow_is_never_bound_to_the_llm_during_autonomous_design() -> None:
    """AC2: the LLM cannot autonomously invoke run_workflow during design -> validate -> repair —
    it is excluded from the tools bound to the model, not merely discouraged by a prompt."""
    run_tool = FakeTool("run_workflow", {"status": "run", "run_status": "COMPLETED"})
    create_tool = FakeTool("create_workflow",
                           {"status": "ready", "definition": {"name": "wf", "nodes": [], "edges": []}})
    script = [FakeAI("Building.", [_call("create_workflow", {"name": "wf"}, "1")]), FakeAI("Done.")]
    client = ScriptedClient(script)
    designer = ToolLoopWorkflowDesigner(FakeLLM(client), [create_tool, run_tool])

    await designer.run("build and run it", [], None)

    bound_names = [getattr(t, "name", "") for t in client.bound]
    assert "run_workflow" not in bound_names
    assert run_tool.calls == []


async def test_strips_fabricated_links_from_narration() -> None:
    create_tool = FakeTool("create_workflow",
                           {"status": "ready", "definition": {"name": "wf", "nodes": [], "edges": []}})
    script = [
        FakeAI("Building.", [_call("create_workflow", {"name": "wf"}, "1")]),
        FakeAI("Created it. View it [here](https://example.com/workflow/fake). Or https://example.com/x"),
    ]
    result = await _designer(script, [create_tool]).run("x", [], None)
    assert "example.com" not in result.content and "http" not in result.content
    assert result.status == DesignerRunResult.CANDIDATE


async def test_loop_carries_the_existing_workflow_id_on_refinement() -> None:
    update_tool = FakeTool("update_workflow",
                           {"status": "ready", "workflow_id": "wf-7",
                            "definition": {"name": "wf", "nodes": [], "edges": []}})
    script = [
        FakeAI("Refining the existing workflow.", [_call("update_workflow", {"workflow_id": "wf-7", "name": "wf"}, "1")]),
        FakeAI("Updated it."),
    ]
    current = {"workflow_id": "wf-7", "workflow": {"name": "old", "nodes": []}}
    result = await _designer(script, [update_tool]).run("add an email step", [], None, current)
    assert result.status == DesignerRunResult.CANDIDATE
    assert result.workflow_id == "wf-7"
    assert update_tool.calls[0]["workflow_id"] == "wf-7"


async def test_update_flow_loads_threads_removals_and_skips_plan_match() -> None:
    """SA-2157: on an existing-workflow edit the model loads via get_workflow, then update_workflow
    carries a PARTIAL diff + remove_node_ids; the diff must not trip the plan-match self-review."""
    get_tool = FakeTool("get_workflow", {
        "status": "loaded", "workflow_id": "wf-7",
        "definition": {"name": "wf", "nodes": [{"id": "a"}, {"id": "b"}], "edges": []}})
    plan_tool = _plan([{"id": "b"}])  # plans only the node it changes
    update_tool = FakeTool("update_workflow", {
        "status": "ready", "workflow_id": "wf-7",
        "definition": {"name": "wf", "nodes": [{"id": "b", "type": "TASK", "name": "B2"}], "edges": []},
        "remove_node_ids": ["a"]})
    script = [
        FakeAI("load", [_call("get_workflow", {"workflow_id": "wf-7"}, "1")]),
        FakeAI("plan", [_call("plan_workflow", {"nodes": []}, "2")]),
        FakeAI("update", [_call("update_workflow", {"workflow_id": "wf-7", "name": "wf"}, "3")]),
        FakeAI("done"),
    ]
    current = {"workflow_id": "wf-7", "workflow": {}}
    sink = RecordingSink()
    result = await _designer(script, [get_tool, plan_tool, update_tool]).run(
        "remove a and tweak b", [], sink, current)

    assert result.status == DesignerRunResult.CANDIDATE
    assert result.workflow_id == "wf-7"
    assert result.remove_node_ids == ["a"]
    assert get_tool.calls[0]["workflow_id"] == "wf-7"
    reviews = [d for e, d in sink.events if e == "review"]
    assert reviews[-1]["ok"] is True  # partial diff not flagged as "missing planned node"


async def test_clone_flow_loads_source_and_threads_source_workflow_id() -> None:
    """SA-2131: clone loads the source via get_workflow, then clone_workflow carries a PARTIAL
    diff + source_workflow_id; it is a NEW workflow (no workflow_id) and the diff must not trip
    the plan-match self-review."""
    get_tool = FakeTool("get_workflow", {
        "status": "loaded", "workflow_id": "wf-src",
        "definition": {"name": "src", "nodes": [{"id": "a"}, {"id": "b"}], "edges": []}})
    plan_tool = _plan([{"id": "b"}])  # plans only the node it changes
    clone_tool = FakeTool("clone_workflow", {
        "status": "ready", "source_workflow_id": "wf-src",
        "definition": {"name": "copy", "nodes": [{"id": "b", "type": "TASK", "name": "B2"}], "edges": []},
        "remove_node_ids": []})
    script = [
        FakeAI("load", [_call("get_workflow", {"workflow_id": "wf-src"}, "1")]),
        FakeAI("plan", [_call("plan_workflow", {"nodes": []}, "2")]),
        FakeAI("clone", [_call("clone_workflow", {"source_workflow_id": "wf-src", "name": "copy"}, "3")]),
        FakeAI("done"),
    ]
    current = {"source_workflow_id": "wf-src", "workflow": {}}
    sink = RecordingSink()
    result = await _designer(script, [get_tool, plan_tool, clone_tool]).run(
        "make a copy but change b", [], sink, current)

    assert result.status == DesignerRunResult.CANDIDATE
    assert result.source_workflow_id == "wf-src"
    assert result.workflow_id == ""                     # a clone is a NEW workflow, not an update
    assert get_tool.calls[0]["workflow_id"] == "wf-src"  # loaded the source as a template
    reviews = [d for e, d in sink.events if e == "review"]
    assert reviews[-1]["ok"] is True                    # partial diff not flagged as "missing node"


def test_review_payload_skips_plan_match_for_update() -> None:
    plan = {"nodes": [{"id": "a"}, {"id": "b"}]}
    partial = {"nodes": [{"id": "b"}]}  # a legitimate update diff (only the changed node)
    assert ToolLoopWorkflowDesigner._review_payload(plan, partial, None, is_update=True) == []
    # the same subset IS a problem for a create (a planned node went missing)
    assert "a" in " ".join(
        ToolLoopWorkflowDesigner._review_payload(plan, partial, None, is_update=False))


async def test_loop_fails_loudly_when_nothing_built() -> None:
    script = [FakeAI("I can't design that.")]
    result = await _designer(script, []).run("???", [], None)
    assert result.status == DesignerRunResult.ERROR
    assert "can't design" in result.error_message


async def test_loop_surfaces_a_rejected_candidate() -> None:
    create_tool = FakeTool("create_workflow", {"status": "invalid", "error": "needs at least 2 nodes"})
    script = [
        FakeAI("Building.", [_call("create_workflow", {"name": "wf"}, "1")]),
        FakeAI("I couldn't finish that."),
    ]
    result = await _designer(script, [create_tool]).run("x", [], None)
    assert result.status == DesignerRunResult.ERROR
    assert "needs at least 2 nodes" in result.error_message


class StreamingClient:
    """astream yields the content in several chunks (real token streaming)."""

    def __init__(self, pieces) -> None:
        self._pieces = pieces

    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        for piece in self._pieces:
            yield AIMessageChunk(content=piece)

    async def ainvoke(self, messages):
        return AIMessageChunk(content="".join(self._pieces))


class NoStreamClient:
    """No astream -> the designer must fall back to ainvoke and still emit thinking."""

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return AIMessageChunk(content="Final answer.")


async def test_streams_tokens_live_as_thinking_deltas() -> None:
    designer = ToolLoopWorkflowDesigner(FakeLLM(StreamingClient(["Hel", "lo ", "world"])), [])
    sink = RecordingSink()
    await designer.run("hi", [], sink)
    deltas = [d["delta"] for e, d in sink.events if e == "thinking"]
    # Each streamed token arrived as its own delta (plus a trailing newline separator).
    assert "Hel" in deltas and "lo " in deltas and "world" in deltas
    assert "".join(deltas).strip() == "Hello world"


async def test_falls_back_to_ainvoke_when_no_astream() -> None:
    designer = ToolLoopWorkflowDesigner(FakeLLM(NoStreamClient()), [])
    sink = RecordingSink()
    result = await designer.run("hi", [], sink)
    thinking = [d["delta"] for e, d in sink.events if e == "thinking"]
    # Fallback emits the whole narration once (not token-by-token).
    assert any("Final answer." in d for d in thinking)
    assert result.status == DesignerRunResult.ERROR  # no workflow built


async def test_loop_uses_conversation_history() -> None:
    create_tool = FakeTool("create_workflow", {
        "status": "ready", "definition": {"name": "wf", "nodes": [], "edges": []},
    })
    script = [
        FakeAI("Building from prior context.", [_call("create_workflow", {"name": "wf"}, "1")]),
        FakeAI("Done."),
    ]
    history = [
        ConversationTurn(role="user", message="make a flow"),
        ConversationTurn(role="system", message="which file?"),
        ConversationTurn(role="user", message="sales.csv"),
    ]
    result = await _designer(script, [create_tool]).run("sales.csv", history, None)
    assert result.status == DesignerRunResult.CANDIDATE


class FakeSeqTool:
    """A tool that returns queued results, one per call (args recorded)."""

    def __init__(self, name, results) -> None:
        self.name = name
        self._results = list(results)
        self.calls: list[dict] = []

    async def ainvoke(self, args):
        self.calls.append(args)
        return self._results.pop(0) if self._results else {"status": "error"}


def _plan(nodes):
    return FakeTool("plan_workflow",
                    {"status": "planned", "nodes": nodes, "rationale": "r", "critique": "c"})


async def test_plan_and_critique_are_emitted_before_create() -> None:
    """AC1: the designer surfaces an explicit plan (+ critique) before any create/update."""
    plan_tool = _plan([{"id": "t", "type": "TRIGGER", "name": "Start"},
                       {"id": "o", "type": "OUTPUT", "name": "End"}])
    create_tool = FakeTool("create_workflow", {
        "status": "ready",
        "definition": {"name": "wf", "nodes": [{"id": "t"}, {"id": "o"}],
                       "edges": [{"source": "t", "target": "o"}]},
    })
    script = [
        FakeAI("Planning.", [_call("plan_workflow", {"nodes": []}, "1")]),
        FakeAI("Building.", [_call("create_workflow", {"name": "wf"}, "2")]),
        FakeAI("Done."),
    ]
    sink = RecordingSink()
    result = await _designer(script, [plan_tool, create_tool]).run("build", [], sink)

    assert result.status == DesignerRunResult.CANDIDATE
    kinds = [e for e, _ in sink.events]
    assert "plan" in kinds and "critique" in kinds
    plan_i = kinds.index("plan")
    create_i = next(i for i, (e, d) in enumerate(sink.events)
                    if e == "tool_call" and d.get("tool") == "create_workflow")
    assert plan_i < create_i


async def test_self_review_passes_when_payload_matches_plan() -> None:
    plan_tool = _plan([{"id": "a"}, {"id": "b"}])
    create_tool = FakeTool("create_workflow", {
        "status": "ready",
        "definition": {"name": "wf", "nodes": [{"id": "a"}, {"id": "b"}], "edges": []},
    })
    script = [
        FakeAI("plan", [_call("plan_workflow", {"nodes": []}, "1")]),
        FakeAI("build", [_call("create_workflow", {"name": "wf"}, "2")]),
        FakeAI("done"),
    ]
    sink = RecordingSink()
    result = await _designer(script, [plan_tool, create_tool]).run("x", [], sink)

    assert result.status == DesignerRunResult.CANDIDATE
    reviews = [d for e, d in sink.events if e == "review"]
    assert reviews and reviews[-1]["ok"] is True and reviews[-1]["issues"] == []


async def test_self_review_feeds_back_a_mismatched_payload_then_succeeds() -> None:
    """AC4: a payload missing a planned node is fed back for reconciliation, not shipped."""
    plan_tool = _plan([{"id": "a"}, {"id": "b"}])
    create_tool = FakeSeqTool("create_workflow", [
        {"status": "ready", "definition": {"name": "wf", "nodes": [{"id": "a"}], "edges": []}},
        {"status": "ready",
         "definition": {"name": "wf", "nodes": [{"id": "a"}, {"id": "b"}], "edges": []}},
    ])
    script = [
        FakeAI("plan", [_call("plan_workflow", {"nodes": []}, "1")]),
        FakeAI("build", [_call("create_workflow", {"name": "wf"}, "2")]),
        FakeAI("fixing", [_call("create_workflow", {"name": "wf"}, "3")]),
        FakeAI("done"),
    ]
    sink = RecordingSink()
    result = await _designer(script, [plan_tool, create_tool]).run("x", [], sink)

    assert result.status == DesignerRunResult.CANDIDATE
    assert result.definition["nodes"] == [{"id": "a"}, {"id": "b"}]
    assert len(create_tool.calls) == 2  # was fed back and retried
    reviews = [d for e, d in sink.events if e == "review"]
    assert any(d["ok"] is False for d in reviews) and reviews[-1]["ok"] is True


def test_review_payload_detects_missing_and_extra() -> None:
    plan = {"nodes": [{"id": "a"}, {"id": "b"}]}
    definition = {"nodes": [{"id": "a"}, {"id": "c"}]}
    issues = " ".join(ToolLoopWorkflowDesigner._review_payload(plan, definition))
    assert "b" in issues and "c" in issues
    # No plan (or a plan without ids) -> nothing to review against -> no false alarm.
    assert ToolLoopWorkflowDesigner._review_payload(None, definition) == []
    assert ToolLoopWorkflowDesigner._review_payload({"nodes": []}, definition) == []


def test_review_payload_flags_a_type_that_is_not_a_catalog_id() -> None:
    """SA-2163: a node must be identified by an exact catalog id (from list_node_types); a bare
    node_type or an invented id is rejected — the client can't resolve/bind it otherwise."""
    known = {"ids": {"worker:http_request", "builtin:output"}}
    definition = {"nodes": [
        {"id": "a", "type": "worker:http_request", "config": {}},                 # real id -> ok
        {"id": "b", "type": "TASK", "config": {}},                                # bare node_type -> flag
        {"id": "c", "type": "worker:ghost", "config": {}},                        # invented id -> flag
    ]}
    issues = " ".join(ToolLoopWorkflowDesigner._review_payload(None, definition, known))
    assert "'b'" in issues and "'c'" in issues
    assert "'a'" not in issues  # a real catalog id is not flagged
    # No catalog knowledge captured -> identity check is a no-op (no false alarm).
    assert ToolLoopWorkflowDesigner._review_payload(None, definition, None) == []


def test_review_payload_flags_missing_required_config() -> None:
    """SA-2163: a node missing a REQUIRED config field (per its schema, keyed by catalog id) is
    rejected; optional/filled fields and schema-less nodes are not."""
    known = {"schemas": {
        "worker:http_request": {"id": "worker:http_request", "node_type": "TASK",
            "config_fields": [{"key": "url", "required": True},
                              {"key": "method", "required": False}]},
        "builtin:output": {"id": "builtin:output", "node_type": "OUTPUT", "config_fields": []},
    }}
    definition = {"nodes": [
        {"id": "a", "type": "worker:http_request", "config": {"url": ""}},
        {"id": "b", "type": "builtin:output", "config": {}},
    ]}
    issues = ToolLoopWorkflowDesigner._review_payload(None, definition, known)
    joined = " ".join(issues)
    assert "url" in joined and "'a'" in joined
    assert "'b'" not in joined  # OUTPUT has no required fields


async def test_self_review_feeds_back_a_bare_node_type_then_succeeds() -> None:
    """SA-2163 regression (workflow 0c389c64…): the agent built the HTTP node as a bare "TASK"
    (not a catalog id), so the client couldn't resolve it and it persisted with worker_type:
    null. The self-review now rejects a non-catalog-id type and accepts it only once the node is
    identified by its catalog id (from which the client binds worker_type/icon/colour)."""
    list_tool = FakeTool("list_node_types", {"node_types": [
        {"id": "builtin:output", "node_type": "OUTPUT"},
        {"id": "worker:http_request", "node_type": "TASK"},
    ]})
    schema_tool = FakeTool("get_node_schema", {"schemas": [
        {"id": "worker:http_request", "node_type": "TASK",
         "config_fields": [{"key": "url", "required": True}, {"key": "method", "required": False}]},
        {"id": "builtin:output", "node_type": "OUTPUT", "config_fields": []},
    ], "missing": []})
    create_tool = FakeSeqTool("create_workflow", [
        # the bug: a bare "TASK" type -> not a catalog id -> unresolvable -> worker_type null
        {"status": "ready", "definition": {"name": "wf", "nodes": [
            {"id": "h", "type": "TASK", "config": {"method": "GET", "url": "https://google.com"}},
            {"id": "o", "type": "builtin:output", "config": {}},
        ], "edges": [{"source": "h", "target": "o"}]}},
        # corrected: the node is identified by its catalog id
        {"status": "ready", "definition": {"name": "wf", "nodes": [
            {"id": "h", "type": "worker:http_request",
             "config": {"method": "GET", "url": "https://google.com"}},
            {"id": "o", "type": "builtin:output", "config": {}},
        ], "edges": [{"source": "h", "target": "o"}]}},
    ])
    script = [
        FakeAI("palette", [_call("list_node_types", {}, "1")]),
        FakeAI("schema", [_call("get_node_schema", {"node_ids": ["worker:http_request", "builtin:output"]}, "2")]),
        FakeAI("build", [_call("create_workflow", {"name": "wf"}, "3")]),
        FakeAI("fix", [_call("create_workflow", {"name": "wf"}, "4")]),
        FakeAI("done"),
    ]
    sink = RecordingSink()
    result = await _designer(script, [list_tool, schema_tool, create_tool]).run("call google", [], sink)

    assert result.status == DesignerRunResult.CANDIDATE
    assert len(create_tool.calls) == 2  # the bare-TASK payload was fed back, not shipped
    reviews = [d for e, d in sink.events if e == "review"]
    assert any(d["ok"] is False for d in reviews) and reviews[-1]["ok"] is True
    bad = next(d for d in reviews if d["ok"] is False)
    assert "catalog id" in " ".join(bad["issues"])
    # the accepted payload identifies the node by its catalog id (client binds worker_type from it)
    assert result.definition["nodes"][0]["type"] == "worker:http_request"


async def test_self_review_feeds_back_an_invented_id_and_missing_config_then_succeeds() -> None:
    """SA-2163 end-to-end: the loop captures the model's own list_node_types / get_node_schema
    results and rejects a first payload that invents a catalog id and leaves a required field
    empty, then accepts the corrected payload."""
    list_tool = FakeTool("list_node_types", {"node_types": [
        {"id": "builtin:output", "node_type": "OUTPUT"},
        {"id": "worker:http_request", "node_type": "TASK"},
    ]})
    schema_tool = FakeTool("get_node_schema", {"schemas": [
        {"id": "worker:http_request", "node_type": "TASK",
         "config_fields": [{"key": "url", "required": True}]},
        {"id": "builtin:output", "node_type": "OUTPUT", "config_fields": []},
    ], "missing": []})
    create_tool = FakeSeqTool("create_workflow", [
        {"status": "ready", "definition": {"name": "wf", "nodes": [
            {"id": "h", "type": "worker:http_request", "config": {}},   # invented? real id, but url missing
            {"id": "x", "type": "worker:madeup", "config": {}},         # invented id
        ], "edges": []}},
        {"status": "ready", "definition": {"name": "wf", "nodes": [
            {"id": "h", "type": "worker:http_request",
             "config": {"url": "https://api.open-meteo.com/v1/forecast"}},
            {"id": "o", "type": "builtin:output", "config": {}},
        ], "edges": [{"source": "h", "target": "o"}]}},
    ])
    script = [
        FakeAI("palette", [_call("list_node_types", {}, "1")]),
        FakeAI("schema", [_call("get_node_schema", {"node_ids": ["worker:http_request", "builtin:output"]}, "2")]),
        FakeAI("build", [_call("create_workflow", {"name": "wf"}, "3")]),
        FakeAI("fix", [_call("create_workflow", {"name": "wf"}, "4")]),
        FakeAI("done"),
    ]
    sink = RecordingSink()
    result = await _designer(script, [list_tool, schema_tool, create_tool]).run("x", [], sink)

    assert result.status == DesignerRunResult.CANDIDATE
    assert len(create_tool.calls) == 2  # first payload was fed back, not shipped
    reviews = [d for e, d in sink.events if e == "review"]
    assert any(d["ok"] is False for d in reviews) and reviews[-1]["ok"] is True
    bad = next(d for d in reviews if d["ok"] is False)
    joined = " ".join(bad["issues"])
    assert "worker:madeup" in joined and "url" in joined

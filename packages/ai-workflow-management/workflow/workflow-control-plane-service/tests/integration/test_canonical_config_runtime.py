"""FE-authored nodes execute correctly end-to-end (integration Module 3).

Every workflow here is authored the way the FE does it — POST
/api/v1/workflows with flat ``parameters`` — then executed through the
real engine (start_run + QueueRunner). This guards the full chain the
NodeSpec redesign rewired: spec-driven split → canonical config keys →
typed handler reads → routing/quorum/iteration behavior.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.value_objects import RunStatus
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok
from tests.helpers.queue_runner import QueueRunner


@pytest.fixture
def container():
    return build_app_container()


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


def _save(client, name, nodes, edges):
    return assert_ok(client.post("/api/v1/workflows", json={
        "name": name, "nodes": nodes, "edges": edges,
    }))["id"]


async def _run(container, workflow_id, input_data=None):
    result = await container["start_run"].execute(
        StartRunCommand(workflow_id=workflow_id, input_data=input_data or {}),
    )
    assert not result.error, result.error
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()
    return container["_infra"].repos.run.get_by_id(result.id)


def _trigger():
    return {"id": "t1", "name": "T", "node_type": "TRIGGER",
            "parameters": {"trigger_type": "manual"}}


def _compute(node_id, expression="1"):
    return {"id": node_id, "name": node_id, "node_type": "COMPUTE",
            "parameters": {"expression": expression}}


def _edge(src, dst, port=""):
    e = {"source_node_id": src, "target_node_id": dst}
    if port:
        e["source_port_id"] = port
    return e


# ── X1: CONDITION structured rules route the true branch ──


@pytest.mark.asyncio
async def test_condition_data_rules_route_true_branch(client, container):
    wf_id = _save(client, "wf-x-cond", [
        _trigger(),
        {"id": "cond", "name": "C", "node_type": "CONDITION",
         "parameters": {"data": [
             {"key": "{{$input.n}}", "operations": ">", "value": 5}]}},
        _compute("ok"), _compute("err"),
    ], [
        _edge("t1", "cond"),
        _edge("cond", "ok", "true_branch"),
        _edge("cond", "err", "false_branch"),
    ])
    run = await _run(container, wf_id, {"n": 7})
    assert "ok" in run.context.completed_nodes
    assert "err" not in run.context.completed_nodes
    assert run.status == RunStatus.COMPLETED


# ── X2: SWITCH structured rules route the named branch ──


@pytest.mark.asyncio
async def test_switch_data_rules_route_named_branch(client, container):
    wf_id = _save(client, "wf-x-switch", [
        _trigger(),
        {"id": "sw", "name": "S", "node_type": "SWITCH",
         "parameters": {"data": [
             {"key": "{{$input.kind}}", "operations": "==",
              "value": "gold", "branch": "vip"},
             {"key": "{{$input.kind}}", "operations": "==",
              "value": "basic", "branch": "std"},
         ]}},
        _compute("vip-node"), _compute("std-node"),
    ], [
        _edge("t1", "sw"),
        _edge("sw", "vip-node", "vip"),
        _edge("sw", "std-node", "std"),
    ])
    run = await _run(container, wf_id, {"kind": "gold"})
    assert "vip-node" in run.context.completed_nodes
    assert "std-node" not in run.context.completed_nodes


# ── X3: MERGE n_of_m quorum via canonical keys ──


@pytest.mark.asyncio
async def test_merge_n_of_m_fires_at_quorum(client, container):
    wf_id = _save(client, "wf-x-merge", [
        _trigger(),
        _compute("b1"), _compute("b2"), _compute("b3"),
        {"id": "merge", "name": "M", "node_type": "MERGE",
         "parameters": {"merge_strategy": "n_of_m", "wait_count": 2}},
        _compute("after"),
    ], [
        _edge("t1", "b1"), _edge("t1", "b2"), _edge("t1", "b3"),
        _edge("b1", "merge"), _edge("b2", "merge"), _edge("b3", "merge"),
        _edge("merge", "after"),
    ])
    run = await _run(container, wf_id)
    assert "after" in run.context.completed_nodes
    assert run.status == RunStatus.COMPLETED


# ── X4: WORKFLOW input_mapping rows → folded child input ──


@pytest.mark.asyncio
async def test_workflow_node_row_array_mapping_feeds_child(client, container):
    child_id = _save(client, "wf-x-child", [
        _trigger(),
        _compute("c-echo", "{{$input.a}} + 0"),
    ], [_edge("t1", "c-echo")])

    parent_id = _save(client, "wf-x-parent", [
        _trigger(),
        {"id": "w", "name": "W", "node_type": "WORKFLOW",
         "parameters": {
             "workflow_id": child_id,
             "timeout_ms": "5000",
             "input_mapping": [
                 {"param_name": "a", "param_value": "{{$input.seed}}"},
             ],
         }},
    ], [_edge("t1", "w")])

    # Sweep contract: timeout_ms is config (number-coerced) on the saved node.
    node = container["_infra"].repos.workflow.get_by_id(parent_id).get_node("w")
    assert node.config["timeout_ms"] == 5000
    assert node.config["input_mapping"] == {"a": "{{$input.seed}}"}

    run = await _run(container, parent_id, {"seed": 5})
    child_runs = [
        r for r in container["_infra"].repos.run.list_by_workflow(child_id)
        if r.parent_run_id == run.id
    ]
    assert child_runs, "child run was not started"
    # The folded row-array mapping resolved into the child input. (The
    # parent's own input_data also rides along by engine design — only
    # the mapped key is under test here.)
    assert child_runs[0].input_data["a"] == 5


# ── X5: LOOP collection iterates per item ──


@pytest.mark.asyncio
async def test_loop_collection_runs_each_iteration(client, container):
    wf_id = _save(client, "wf-x-loop", [
        _trigger(),
        {"id": "loop", "name": "L", "node_type": "LOOP",
         "parameters": {"loop_type": "collection",
                        "collection": "{{$input.items}}",
                        "mode": "sequential"}},
        _compute("body", "{{$loop.index}} + 0"),
        _compute("done"),
    ], [
        _edge("t1", "loop"),
        _edge("loop", "body", "loop_body"),
        _edge("loop", "done", "loop_complete"),
    ])
    run = await _run(container, wf_id, {"items": [10, 20, 30]})
    # Iteration tasks share one deterministic id (repo upsert), so task
    # count can't witness iterations — the loop driver's state can.
    state = run.context.loop_state.get("loop") or {}
    assert state.get("total") == 3
    assert sorted(state.get("completed_iterations") or []) == [0, 1, 2]
    assert "done" in run.context.completed_nodes
    assert "body" in run.context.completed_nodes


# ── X6: SET_VARIABLE feeds {{$variables.<name>}} into a CONDITION ──


@pytest.mark.asyncio
async def test_set_variable_routes_condition_via_vars_namespace(client, container):
    """SET_VARIABLE authored via parameters lands the declared variable in
    the run's ``__vars`` namespace, and a downstream CONDITION gated on
    ``{{$variables.<name>}}`` resolves it and routes the true branch.

    This guards the engine fix that activates the run ``ResolutionScope``
    on the node-execution path (``orchestrate_execute_node`` →
    ``_exec_uniform``). Previously the ambient scope was unset there, so
    the ``SystemVariablesFrame`` was unreachable and the rule silently
    evaluated False — the CONDITION took the else branch regardless."""
    wf_id = _save(client, "wf-x-setvar", [
        _trigger(),
        {"id": "sv", "name": "SV", "node_type": "SET_VARIABLE",
         "parameters": {"variables": [
             {"name": "greet", "type": "string", "value_expression": "hi"}]}},
        {"id": "cond", "name": "C", "node_type": "CONDITION",
         "parameters": {"data": [
             {"key": "{{$variables.greet}}", "operations": "==",
              "value": "hi"}]}},
        _compute("ok"), _compute("err"),
    ], [
        _edge("t1", "sv"),
        _edge("sv", "cond"),
        _edge("cond", "ok", "true_branch"),
        _edge("cond", "err", "false_branch"),
    ])
    run = await _run(container, wf_id)
    assert "sv" in run.context.completed_nodes
    # The CONDITION resolved {{$variables.greet}} == "hi" → true branch.
    assert "ok" in run.context.completed_nodes
    assert "err" not in run.context.completed_nodes
    assert run.status == RunStatus.COMPLETED

    # Secondary: the variable is materialized in the __vars namespace.
    view = container["run_context_view_store"].get(run.id)
    vars_entry = view.variables.get("__vars") or {}
    assert vars_entry.get("__file_ref") is True
    assert vars_entry.get("columns") == ["greet"]


# ── X6b: {{$variables.<name>}} resolves in SWITCH and COMPUTE too ──


@pytest.mark.asyncio
async def test_vars_namespace_resolves_in_switch_and_compute(client, container):
    """The scope-activation fix is uniform across handlers — every node
    executes through ``_exec_uniform``. A SET_VARIABLE feeds both a SWITCH
    (structured rule keyed on ``{{$variables.kind}}``) and a COMPUTE
    (expression reading ``{{$variables.factor}}``)."""
    wf_id = _save(client, "wf-x-vars-multi", [
        _trigger(),
        {"id": "sv", "name": "SV", "node_type": "SET_VARIABLE",
         "parameters": {"variables": [
             {"name": "kind", "type": "string", "value_expression": "gold"},
             {"name": "factor", "type": "number", "value_expression": "3"},
         ]}},
        {"id": "sw", "name": "S", "node_type": "SWITCH",
         "parameters": {"data": [
             {"key": "{{$variables.kind}}", "operations": "==",
              "value": "gold", "branch": "vip"},
             {"key": "{{$variables.kind}}", "operations": "==",
              "value": "basic", "branch": "std"},
         ]}},
        _compute("vip-node", "{{$variables.factor}} * 2"),
        _compute("std-node"),
    ], [
        _edge("t1", "sv"),
        _edge("sv", "sw"),
        _edge("sw", "vip-node", "vip"),
        _edge("sw", "std-node", "std"),
    ])
    run = await _run(container, wf_id)
    # SWITCH resolved {{$variables.kind}} == "gold" → vip branch.
    assert "vip-node" in run.context.completed_nodes
    assert "std-node" not in run.context.completed_nodes

    # COMPUTE on the vip branch resolved {{$variables.factor}} (= 3) * 2.
    view = container["run_context_view_store"].get(run.id)
    entry = view.variables.get("vip-node") or {}
    columns = [str(c) for c in (entry.get("columns") or [])]
    assert any("output.result" in c for c in columns), columns


# ── X6c: {{$variables.<name>}} resolves on the /node/test path too ──


@pytest.mark.asyncio
async def test_vars_namespace_resolves_in_single_node_test(client, container):
    """The scope-activation fix extends to the ``/node/test`` path.

    ``TestNodeUseCase`` calls ``handler.handle()`` directly — it deliberately
    bypasses ``_exec_uniform`` to keep a single-node test isolated and
    fan-out-free — so it must activate the run ``ResolutionScope`` itself.
    Without that, a CONDITION tested in isolation cannot reach the ambient
    ``SystemVariablesFrame``: ``{{$variables.<name>}}`` resolves to nothing
    and the structured rule silently evaluates False (mis-routing).

    Flow mirrors the real user flow: test the SET_VARIABLE node first (seeds
    ``__vars`` into the persisted test run + view), then test the downstream
    CONDITION node and assert it resolved the variable and evaluated True.
    Reverting the scope wrapper in ``TestNodeUseCase`` flips ``result`` to
    False — the negative control for this test."""
    from app.layer2_application.features.test_node.use_cases.test_node_use_case import (
        TestNodeCommand,
    )

    wf_id = _save(client, "wf-x-setvar-nodetest", [
        _trigger(),
        {"id": "sv", "name": "SV", "node_type": "SET_VARIABLE",
         "parameters": {"variables": [
             {"name": "greet", "type": "string", "value_expression": "hi"}]}},
        {"id": "cond", "name": "C", "node_type": "CONDITION",
         "parameters": {"data": [
             {"key": "{{$variables.greet}}", "operations": "==",
              "value": "hi"}]}},
        _compute("ok"), _compute("err"),
    ], [
        _edge("t1", "sv"),
        _edge("sv", "cond"),
        _edge("cond", "ok", "true_branch"),
        _edge("cond", "err", "false_branch"),
    ])

    test_node = container["test_node"]

    # Seed __vars: testing the SET_VARIABLE node writes the declared
    # variable into the (persisted) test run's __vars namespace + view.
    sv_result = await test_node.execute(
        TestNodeCommand(workflow_id=wf_id, node_id="sv"),
    )
    assert not sv_result.error, sv_result.error

    # Now test the CONDITION node in isolation. With the scope-activation
    # fix it resolves {{$variables.greet}} == "hi" → True. Without it the
    # ambient SystemVariablesFrame is unreachable and result is False.
    cond_result = await test_node.execute(
        TestNodeCommand(workflow_id=wf_id, node_id="cond"),
    )
    assert not cond_result.error, cond_result.error
    assert cond_result.execution_type == "inline"
    output = cond_result.handler_output.get("output") or {}
    assert output.get("result") is True, cond_result.handler_output
    # The rule key was actually resolved, not left as the raw template.
    assert "{{$variables.greet}}" not in str(output.get("resolved", ""))


# ── X7: COMPUTE expression from parameters with output trace ──


@pytest.mark.asyncio
async def test_compute_parameters_produce_output_trace(client, container):
    wf_id = _save(client, "wf-x-compute", [
        _trigger(),
        _compute("calc", "{{$input.price}} * 2"),
    ], [_edge("t1", "calc")])
    run = await _run(container, wf_id, {"price": 21})
    assert "calc" in run.context.completed_nodes

    view = container["run_context_view_store"].get(run.id)
    entry = view.variables.get("calc") or {}
    columns = [str(c) for c in (entry.get("columns") or [])]
    # Output payload carries result + the evaluation trace.
    assert any("output.result" in c for c in columns), columns
    assert any("output.resolved" in c for c in columns), columns


# ── X8: condition LOOP re-resolves {{$variables.x}} on each iteration ──


@pytest.mark.asyncio
async def test_condition_loop_reresolves_vars_each_iteration(client, container):
    """A condition LOOP re-evaluates its ``condition`` in
    ``iteration_complete`` — the worker-dequeued
    ``LoopIterationAction`` / ``orchestrate_iteration`` path. Unlike the
    loop's ``initialize_loop`` (which runs under ``_exec_uniform``'s scope),
    that path set no ambient ResolutionScope, so a condition gated on
    ``{{$variables.keep}}`` resolved on the first pass but raised
    ``VariableResolutionError`` on every later iteration → the loop bailed
    out early via ``early_exit_error``.

    ``keep`` is truthy, so with the scope fix the loop runs all
    ``max_iterations`` (2) iterations; without it it stops after iteration 0
    (``completed_iterations == [0]``). This is the loop analogue of X6 — the
    same missing-scope class as the original ``orchestrate_execute_node`` bug,
    on a different ``orchestrate_*`` entry point."""
    wf_id = _save(client, "wf-x-cond-loop-vars", [
        _trigger(),
        {"id": "sv", "name": "SV", "node_type": "SET_VARIABLE",
         "parameters": {"variables": [
             {"name": "keep", "type": "boolean", "value_expression": "true"}]}},
        {"id": "loop", "name": "L", "node_type": "LOOP",
         "parameters": {"loop_type": "condition",
                        "condition": "{{$variables.keep}}",
                        "max_iterations": 2,
                        "mode": "sequential"}},
        _compute("body", "{{$loop.index}} + 0"),
        _compute("done"),
    ], [
        _edge("t1", "sv"),
        _edge("sv", "loop"),
        _edge("loop", "body", "loop_body"),
        _edge("loop", "done", "loop_complete"),
    ])
    run = await _run(container, wf_id)
    state = run.context.loop_state.get("loop") or {}
    # Scope fix: condition resolves truthy each iteration → both run.
    # Without it: iteration_complete raises on {{$variables.keep}} and the
    # loop early-exits after iteration 0.
    assert sorted(state.get("completed_iterations") or []) == [0, 1], state
    assert "done" in run.context.completed_nodes


# ── X9: PENDING-node replay re-request re-resolves {{$variables.x}} ──


@pytest.mark.asyncio
async def test_rerequest_pending_node_resolves_vars(client, container):
    """``rerequest_pending_node`` (the run-replay / rerun re-request path)
    re-runs a PENDING node's handler. It hydrates the view into the aggregate
    but — before this fix — set no ambient ResolutionScope, so an INPUT whose
    ``input_binding`` references ``{{$variables.x}}`` failed on replay:
    ``resolve_prefill`` re-raises ``VariableResolutionError`` as
    ``ExpressionError``. The *first* request works (it runs under
    ``_exec_uniform``'s scope); only the replay path was broken.

    With the fix, re-requesting resolves ``$variables`` again and the fresh
    PENDING task carries the resolved prefill. Without it, the call raises
    ExpressionError — the negative control."""
    wf_id = _save(client, "wf-x-rereq-vars", [
        _trigger(),
        {"id": "sv", "name": "SV", "node_type": "SET_VARIABLE",
         "parameters": {"variables": [
             {"name": "greet", "type": "string", "value_expression": "hello"}]}},
        {"id": "inp", "name": "I", "node_type": "INPUT",
         "parameters": {
             "instruction": "Provide",
             "input_schema": [{"key": "greeting", "type": "string", "label": "G"}],
             "input_binding": {"greeting": "{{$variables.greet}}"},
         }},
    ], [
        _edge("t1", "sv"),
        _edge("sv", "inp"),
    ])
    run = await _run(container, wf_id)  # pauses at INPUT (PENDING)

    engine = container["_orchestration_worker"]._engine
    workflow = container["_infra"].repos.workflow.get_by_id(wf_id)
    node = workflow.get_node("inp")
    topology = run.context.topology

    # The replay re-request path. With the scope fix this resolves
    # {{$variables.greet}}; without it, resolve_prefill raises ExpressionError.
    handler_result = await engine.rerequest_pending_node(
        node, run, workflow, topology,
    )
    # Without the fix this call RAISES ExpressionError (resolve_prefill
    # re-raises the VariableResolutionError) — so simply returning a task
    # with the resolved value, not the raw template, is the proof.
    assert handler_result is not None
    assert handler_result.tasks_to_dispatch, "no fresh PENDING task created"
    new_task = handler_result.tasks_to_dispatch[0]
    prefilled = (new_task.output_metadata or {}).get("prefilled") or {}
    blob = json.dumps(prefilled)
    assert "hello" in blob, prefilled                       # $variables resolved
    assert "{{$variables.greet}}" not in blob, prefilled    # not the raw template

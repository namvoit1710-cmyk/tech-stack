"""OUTPUT node defines the workflow's output contract.

When a workflow declares OUTPUT node(s), their resolved fields ARE the
workflow's output:

* Run-level: ``run.output_data`` carries only the OUTPUT node entries
  (``OrchestrationEngine._build_output_data``).
* Sub-workflow: the parent WORKFLOW node receives the OUTPUT fields merged
  flat, addressable as ``{{$<wf-node>.output.<field>}}`` with no child
  node-id hop (``ChildWorkflowCoordinator._complete_parent_locked``).

Without an OUTPUT node — or when every OUTPUT node was skipped on an
untaken branch — the legacy shape is preserved: all node outputs keyed by
child node id.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.resolve.variable_resolver import (
    set_current_run_id,
    reset_current_run_id,
)
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)
from bootstrap import build_app_container
from tests.helpers.queue_runner import QueueRunner


@pytest.fixture
def container():
    return build_app_container()


async def _drain(container) -> None:
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()


async def _start_and_drain(container, workflow_id: str, input_data=None) -> str:
    result = await container["start_run"].execute(
        StartRunCommand(workflow_id=workflow_id, input_data=input_data or {})
    )
    assert not result.error, f"start_run failed: {result.error}"
    await _drain(container)
    return result.id


def _resolver(container):
    return container["_orchestration_worker"]._engine._variable_resolver


def _child_with_output() -> Workflow:
    """TRIGGER → COMPUTE(21 + 21) → OUTPUT(result, message)."""
    return Workflow(
        id="child-out-wf", name="child-out",
        nodes=[
            WorkflowNode(id="child-trig", name="ct", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="child-compute", name="cc", node_type=NodeType.COMPUTE,
                config={"expression": "21 + 21"},
            ),
            WorkflowNode(
                id="child-output", name="co", node_type=NodeType.OUTPUT,
                input_binding={
                    "result": "{{$child-compute.output.result}}",
                    "message": "done",
                },
                config={
                    "output_schema": [
                        {"key": "result", "type": "number"},
                        {"key": "message", "type": "string"},
                    ],
                },
            ),
        ],
        edges=[
            WorkflowEdge(id="ce1", source_node_id="child-trig", target_node_id="child-compute"),
            WorkflowEdge(id="ce2", source_node_id="child-compute", target_node_id="child-output"),
        ],
    )


def _parent(child_id: str, output_binding: dict | None = None) -> Workflow:
    config = {"workflow_id": child_id, "input_mapping": {}, "on_failure": "fail"}
    if output_binding is not None:
        config["output_binding"] = output_binding
    return Workflow(
        id="parent-wf", name="parent",
        nodes=[
            WorkflowNode(id="p-trig", name="pt", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(id="wf-node", name="wn", node_type=NodeType.WORKFLOW, config=config),
        ],
        edges=[WorkflowEdge(id="pe1", source_node_id="p-trig", target_node_id="wf-node")],
    )


@pytest.mark.asyncio
async def test_child_with_output_node_returns_flat_fields(container):
    """Child OUTPUT fields surface flat under the parent WORKFLOW node —
    no child node-id hop — and non-OUTPUT child nodes are not exposed."""
    repo = container["_infra"].repos.workflow
    repo.save(_child_with_output())
    repo.save(_parent("child-out-wf"))

    parent_run_id = await _start_and_drain(container, "parent-wf")

    parent_view = container["run_context_view_store"].get(parent_run_id)
    variables = dict(parent_view.variables)
    resolver = _resolver(container)
    tok = set_current_run_id(parent_run_id)
    try:
        assert await resolver.resolve("{{$wf-node.output.result}}", variables) == 42
        assert await resolver.resolve("{{$wf-node.output.message}}", variables) == "done"

        full = await resolver.resolve("{{$wf-node.output}}", variables)
        assert set(full.keys()) == {"result", "message"}, (
            f"expected only OUTPUT fields, got: {sorted(full.keys())}"
        )

        # The metadata envelope survives the flat lift.
        child_run_id = await resolver.resolve(
            "{{$wf-node.metadata.child_run_id}}", variables,
        )
        assert child_run_id
    finally:
        reset_current_run_id(tok)

    # The child run's own output_data honors the contract too.
    run_repo = container["_infra"].repos.run
    child_run = run_repo.get_by_id(
        run_repo.get_by_id(parent_run_id).context.child_runs["wf-node"]
    )
    assert child_run.status == RunStatus.COMPLETED
    assert set(child_run.output_data.keys()) == {"child-output"}


@pytest.mark.asyncio
async def test_child_without_output_node_returns_all(container):
    """No OUTPUT node → legacy shape: all child node outputs keyed by id."""
    repo = container["_infra"].repos.workflow
    repo.save(Workflow(
        id="child-plain-wf", name="child-plain",
        nodes=[
            WorkflowNode(id="child-trig", name="ct", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="child-compute", name="cc", node_type=NodeType.COMPUTE,
                config={"expression": "21 + 21"},
            ),
        ],
        edges=[WorkflowEdge(id="ce1", source_node_id="child-trig", target_node_id="child-compute")],
    ))
    repo.save(_parent("child-plain-wf"))

    parent_run_id = await _start_and_drain(container, "parent-wf")

    parent_view = container["run_context_view_store"].get(parent_run_id)
    variables = dict(parent_view.variables)
    resolver = _resolver(container)
    tok = set_current_run_id(parent_run_id)
    try:
        assert await resolver.resolve(
            "{{$wf-node.output.child-compute.result}}", variables,
        ) == 42
    finally:
        reset_current_run_id(tok)


@pytest.mark.asyncio
async def test_skipped_output_node_falls_back_to_all(container):
    """An OUTPUT node on an untaken branch produced nothing → fall back to
    the keyed-by-node-id shape rather than returning an empty contract."""
    repo = container["_infra"].repos.workflow
    repo.save(Workflow(
        id="child-skip-wf", name="child-skip",
        nodes=[
            WorkflowNode(
                id="cond1", name="Condition", node_type=NodeType.CONDITION,
                config={"expression": "{{$input.value}} == 23"},
            ),
            WorkflowNode(
                id="child-compute", name="cc", node_type=NodeType.COMPUTE,
                config={"expression": "21 + 21"},
            ),
            WorkflowNode(
                id="child-output", name="co", node_type=NodeType.OUTPUT,
                input_binding={"never": "ran"},
                config={"output_schema": [{"key": "never", "type": "string"}]},
            ),
        ],
        edges=[
            WorkflowEdge(id="se1", source_node_id="cond1", target_node_id="child-compute",
                         source_port_id="true_branch"),
            WorkflowEdge(id="se2", source_node_id="cond1", target_node_id="child-output",
                         source_port_id="false_branch"),
        ],
    ))
    repo.save(_parent("child-skip-wf"))

    parent_run_id = await _start_and_drain(container, "parent-wf", {"value": 23})

    run_repo = container["_infra"].repos.run
    parent_run = run_repo.get_by_id(parent_run_id)
    assert parent_run.status == RunStatus.COMPLETED

    parent_view = container["run_context_view_store"].get(parent_run_id)
    variables = dict(parent_view.variables)
    resolver = _resolver(container)
    tok = set_current_run_id(parent_run_id)
    try:
        full = await resolver.resolve("{{$wf-node.output}}", variables)
        assert "child-compute" in full, f"got keys: {sorted(full.keys())}"
        assert "never" not in full
        assert await resolver.resolve(
            "{{$wf-node.output.child-compute.result}}", variables,
        ) == 42
    finally:
        reset_current_run_id(tok)


@pytest.mark.asyncio
async def test_run_level_output_data_contains_only_output_entries(container):
    """Standalone run with an OUTPUT node: ``run.output_data`` carries only
    the OUTPUT node entry — no ``input``, no other node ids."""
    container["_infra"].repos.workflow.save(_child_with_output())

    run_id = await _start_and_drain(container, "child-out-wf")

    run = container["_infra"].repos.run.get_by_id(run_id)
    assert run.status == RunStatus.COMPLETED
    assert set(run.output_data.keys()) == {"child-output"}, (
        f"expected only the OUTPUT node entry, got: {sorted(run.output_data.keys())}"
    )


@pytest.mark.asyncio
async def test_multiple_output_nodes_merge_flat_later_wins(container):
    """Two executed OUTPUT nodes merge in definition order; the later-defined
    node wins on key collision."""
    repo = container["_infra"].repos.workflow
    repo.save(Workflow(
        id="child-multi-wf", name="child-multi",
        nodes=[
            WorkflowNode(id="child-trig", name="ct", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="out-a", name="oa", node_type=NodeType.OUTPUT,
                input_binding={"shared": "from-a", "a_only": "A"},
                config={"output_schema": [
                    {"key": "shared", "type": "string"},
                    {"key": "a_only", "type": "string"},
                ]},
            ),
            WorkflowNode(
                id="out-b", name="ob", node_type=NodeType.OUTPUT,
                input_binding={"shared": "from-b", "b_only": "B"},
                config={"output_schema": [
                    {"key": "shared", "type": "string"},
                    {"key": "b_only", "type": "string"},
                ]},
            ),
        ],
        edges=[
            WorkflowEdge(id="me1", source_node_id="child-trig", target_node_id="out-a"),
            WorkflowEdge(id="me2", source_node_id="child-trig", target_node_id="out-b"),
        ],
    ))
    repo.save(_parent("child-multi-wf"))

    parent_run_id = await _start_and_drain(container, "parent-wf")

    parent_view = container["run_context_view_store"].get(parent_run_id)
    variables = dict(parent_view.variables)
    resolver = _resolver(container)
    tok = set_current_run_id(parent_run_id)
    try:
        full = await resolver.resolve("{{$wf-node.output}}", variables)
        assert full.get("a_only") == "A"
        assert full.get("b_only") == "B"
        assert full.get("shared") == "from-b", f"later OUTPUT should win, got {full.get('shared')!r}"
    finally:
        reset_current_run_id(tok)


@pytest.mark.asyncio
async def test_output_binding_overlay_applies_on_flat_output(container):
    """A parent-side ``output_binding`` still overlays the flat OUTPUT dict."""
    repo = container["_infra"].repos.workflow
    repo.save(_child_with_output())
    repo.save(_parent(
        "child-out-wf",
        output_binding={"picked": "{{$child.child-output.output.result}}"},
    ))

    parent_run_id = await _start_and_drain(container, "parent-wf")

    parent_view = container["run_context_view_store"].get(parent_run_id)
    variables = dict(parent_view.variables)
    resolver = _resolver(container)
    tok = set_current_run_id(parent_run_id)
    try:
        full = await resolver.resolve("{{$wf-node.output}}", variables)
        assert full.get("result") == 42
        assert full.get("picked") == 42, f"output_binding overlay missing: {full}"
    finally:
        reset_current_run_id(tok)

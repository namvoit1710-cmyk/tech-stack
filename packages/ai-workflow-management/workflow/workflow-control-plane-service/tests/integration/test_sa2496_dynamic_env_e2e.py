"""SA-2496 E2E — "Dynamic environment (only id)" on the call-subflow node. (D14 run-through covered here + via FE)

Runs a real parent workflow through the full app container (bootstrap, memory
repos, real OrchestrationEngine + ChildWorkflowCoordinator) via StartRun — the
same path a trigger/schedule funnels through:

    parent:  TRIGGER --> WORKFLOW(sub, environment_id="{{$input.env_id}}")
    sub:     TRIGGER   (trivial; just needs its $env bound)

Cases:
* env_id resolves to an ASSIGNED env id → child run binds that env's $env, parent COMPLETES.
* env_id resolves to a NON-assigned id / a name → node fails ENVIRONMENT_NOT_ASSIGNED.
* env_id unresolvable (missing $input key) → node fails DYNAMIC_ENV_UNRESOLVED.
"""
from __future__ import annotations

import asyncio

import pytest

import bootstrap
from app.layer1_domain.entities.environment import Environment
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_environment_assignment import (
    WorkflowEnvironmentAssignment,
)
from app.layer1_domain.value_objects.env_variable import EnvVariable
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)


async def _drain(container, max_idle_rounds: int = 3) -> None:
    queue = container["_orchestration_queue"]
    worker = container["_orchestration_worker"]
    aq = queue._queue
    idle = 0
    for _ in range(2000):
        if aq.empty():
            idle += 1
            if idle >= max_idle_rounds:
                return
            await asyncio.sleep(0)
            continue
        idle = 0
        action = await asyncio.wait_for(queue.dequeue(), timeout=2.0)
        await worker._dispatch(action)
    raise AssertionError("queue did not settle")


def _sub() -> Workflow:
    return Workflow(
        id="sub-1", name="GitHub root-cause (sub)",
        nodes=[WorkflowNode(id="s-start", name="S-START",
                            node_type=NodeType.TRIGGER, config={})],
        edges=[],
    )


def _parent(env_expr: str) -> Workflow:
    return Workflow(
        id="parent-1", name="[FIX] parent",
        nodes=[
            WorkflowNode(id="p-start", name="P-START",
                         node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(id="p-call", name="Call GitHub root-cause",
                         node_type=NodeType.WORKFLOW,
                         config={"workflow_id": "sub-1", "environment_id": env_expr}),
        ],
        edges=[WorkflowEdge(id="p-e1", source_node_id="p-start",
                            target_node_id="p-call")],
    )


def _seed(container, *, env_expr: str):
    repos = container["_infra"].repos
    repos.workflow.save(_sub())
    repos.workflow.save(_parent(env_expr))
    env = Environment.create(
        tenant_id="system", section="Root Cause", name="QAS", created_by="test",
        variables=[EnvVariable(key="BASE_URL", value="https://qas.example")],
    )
    repos.environment.save(env)
    assignment = WorkflowEnvironmentAssignment.create(
        tenant_id="system", workflow_id="sub-1", section="Root Cause",
        environment_ids=[env.id], created_by="test",
    )
    repos.workflow_environment.save(assignment)
    return env


@pytest.fixture
def container():
    return bootstrap.build_app_container()


@pytest.mark.asyncio
async def test_dynamic_env_id_binds_child(container):
    env = _seed(container, env_expr="{{$input.env_id}}")
    run_repo = container["_infra"].repos.run
    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="parent-1", input_data={"env_id": env.id}),
    )
    assert not result.error, f"start_run failed: {result.error}"
    await _drain(container)

    parent = run_repo.get_by_id(result.id)
    child_id = parent.context.child_runs.get("p-call")
    assert child_id, f"no child spawned; child_runs={parent.context.child_runs}"
    child = run_repo.get_by_id(child_id)
    assert child.context.variables.get("env") == {"BASE_URL": "https://qas.example"}, (
        f"child $env not bound to the resolved id; got {child.context.variables.get('env')}"
    )


@pytest.mark.asyncio
async def test_dynamic_env_wrong_id_not_assigned(container):
    _seed(container, env_expr="{{$input.env_id}}")
    run_repo = container["_infra"].repos.run
    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="parent-1", input_data={"env_id": "env-wrong"}),
    )
    await _drain(container)
    parent = run_repo.get_by_id(result.id)
    assert parent.status == RunStatus.FAILED, f"expected FAILED; got {parent.status}"
    assert "ENVIRONMENT_NOT_ASSIGNED" in (parent.error or ""), (
        f"expected ENVIRONMENT_NOT_ASSIGNED; got {parent.error!r}"
    )


@pytest.mark.asyncio
async def test_dynamic_env_name_rejected_id_only(container):
    _seed(container, env_expr="{{$input.env_id}}")
    run_repo = container["_infra"].repos.run
    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="parent-1", input_data={"env_id": "QAS"}),
    )
    await _drain(container)
    parent = run_repo.get_by_id(result.id)
    assert parent.status == RunStatus.FAILED
    assert "ENVIRONMENT_NOT_ASSIGNED" in (parent.error or "")


@pytest.mark.asyncio
async def test_dynamic_env_unresolved_fails(container):
    _seed(container, env_expr="{{$input.env_id}}")
    run_repo = container["_infra"].repos.run
    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="parent-1", input_data={}),  # no env_id
    )
    await _drain(container)
    parent = run_repo.get_by_id(result.id)
    assert parent.status == RunStatus.FAILED, f"expected FAILED; got {parent.status}"
    assert "DYNAMIC_ENV_UNRESOLVED" in (parent.error or ""), (
        f"expected DYNAMIC_ENV_UNRESOLVED; got {parent.error!r}"
    )

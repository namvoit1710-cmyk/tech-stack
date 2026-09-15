"""Unit tests for the get_run_execution_log feature."""

import pytest

from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.get_run_execution_log.use_cases.get_run_execution_log_use_case import (
    GetRunExecutionLogCommand,
    GetRunExecutionLogUseCase,
)
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from tests.helpers.view_store_doubles import LiveViewStoreDouble


def _env(run_id, event_type, *, ts, event_id, payload=None, topic="t"):
    return EventLogEnvelope(
        event_id=event_id,
        run_id=run_id,
        topic=topic,
        event_type=event_type,
        timestamp=ts,
        payload=payload or {},
    )


def _uc(logger, run_repo, event_log_repo):
    return GetRunExecutionLogUseCase(
        logger=logger,
        event_log_repo=event_log_repo,
        run_context_view_store=LiveViewStoreDouble(run_repo),
    )


@pytest.fixture
def event_log_repo():
    return MemoryEventLogRepository()


@pytest.fixture
def run(run_repo):
    """A single RUNNING run in the repo."""
    r = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run_repo.save(r)
    return r


def _seed_single_run(event_log_repo):
    # Appended in timestamp order so per-run sequence aligns with order.
    event_log_repo.append(_env("run-1", "RunStatusChanged", ts="2026-05-29T00:00:01Z", event_id="e1", payload={"status": "started"}))
    event_log_repo.append(_env("run-1", "NodeDispatched", ts="2026-05-29T00:00:02Z", event_id="e2", payload={"node_id": "a", "task_id": "t1"}))
    event_log_repo.append(_env("run-1", "NodeCompleted", ts="2026-05-29T00:00:03Z", event_id="e3", payload={"node_id": "a"}))
    event_log_repo.append(_env("run-1", "NodeFailed", ts="2026-05-29T00:00:04Z", event_id="e4", payload={"node_id": "b"}))


@pytest.mark.asyncio
async def test_happy_path_ordered_with_summary(logger, run_repo, run, event_log_repo):
    _seed_single_run(event_log_repo)
    uc = _uc(logger, run_repo, event_log_repo)

    result = await uc.execute(GetRunExecutionLogCommand(run_id="run-1"))

    assert result.error is None
    # Ordered by (timestamp, run_id, sequence); index is sequential.
    assert [e["event_type"] for e in result.events] == [
        "RunStatusChanged", "NodeDispatched", "NodeCompleted", "NodeFailed",
    ]
    assert [e["index"] for e in result.events] == [0, 1, 2, 3]
    assert all(e["run_id"] == "run-1" and e["depth"] == 0 for e in result.events)
    assert result.total == 4

    summary = result.summary
    assert summary["run_id"] == "run-1"
    assert summary["workflow_id"] == "wf-1"
    assert summary["status"] == "RUNNING"
    assert summary["total_runs"] == 1
    assert summary["run_ids"] == ["run-1"]
    assert summary["total_events"] == 4
    assert summary["node_counts"] == {"completed": 1, "failed": 1, "skipped": 0}


@pytest.mark.asyncio
async def test_within_run_order_follows_append_not_timestamp(logger, run_repo, run, event_log_repo):
    # Within a run, list_by_run order (append/sequence) is authoritative.
    # Here timestamps are out of order vs append order; the result must
    # still follow append/sequence order, not the timestamps.
    event_log_repo.append(_env("run-1", "NodeDispatched", ts="2026-05-29T00:00:02Z", event_id="e1", payload={"node_id": "a"}))
    event_log_repo.append(_env("run-1", "NodeCompleted", ts="2026-05-29T00:00:01Z", event_id="e2", payload={"node_id": "a"}))
    uc = _uc(logger, run_repo, event_log_repo)

    result = await uc.execute(GetRunExecutionLogCommand(run_id="run-1"))
    assert [e["sequence"] for e in result.events] == [1, 2]
    assert [e["event_type"] for e in result.events] == ["NodeDispatched", "NodeCompleted"]


@pytest.mark.asyncio
async def test_cross_run_merge_handles_mixed_timestamp_formats(logger, run_repo, event_log_repo):
    # Parent uses 'Z', child uses '+00:00' with microseconds. A lexicographic
    # string compare would mis-order these ('.' < 'Z'); a parsed compare must
    # interleave them by actual instant.
    parent = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING,
                         context=RunContext(child_runs={"sub": "child-1"}))
    child = WorkflowRun(id="child-1", workflow_id="wf-child", status=RunStatus.COMPLETED,
                        parent_run_id="run-1", depth_level=1, root_run_id="run-1")
    run_repo.save(parent)
    run_repo.save(child)
    event_log_repo.append(_env("run-1", "RunStatusChanged", ts="2026-05-29T00:00:01Z", event_id="p1", payload={"status": "started"}))
    event_log_repo.append(_env("child-1", "RunStatusChanged", ts="2026-05-29T00:00:01.500000+00:00", event_id="c1", payload={"status": "started"}))
    event_log_repo.append(_env("run-1", "NodeCompleted", ts="2026-05-29T00:00:02Z", event_id="p2", payload={"node_id": "a"}))

    uc = _uc(logger, run_repo, event_log_repo)
    result = await uc.execute(GetRunExecutionLogCommand(run_id="run-1"))
    # Chronological: parent@01Z, child@01.5, parent@02Z.
    assert [e["run_id"] for e in result.events] == ["run-1", "child-1", "run-1"]


@pytest.mark.asyncio
async def test_run_not_found(logger, run_repo, event_log_repo):
    uc = _uc(logger, run_repo, event_log_repo)
    result = await uc.execute(GetRunExecutionLogCommand(run_id="missing"))
    assert result.error_code == ErrorCode.RUN_NOT_FOUND.code
    assert result.error_details == {"run_id": "missing"}
    assert result.events == []


@pytest.mark.asyncio
async def test_filter_event_type(logger, run_repo, run, event_log_repo):
    _seed_single_run(event_log_repo)
    uc = _uc(logger, run_repo, event_log_repo)

    result = await uc.execute(
        GetRunExecutionLogCommand(run_id="run-1", event_type="NodeFailed"),
    )
    assert [e["event_type"] for e in result.events] == ["NodeFailed"]
    assert result.total == 1
    # Summary still describes the FULL run, not the filtered slice.
    assert result.summary["total_events"] == 4


@pytest.mark.asyncio
async def test_filter_node_id(logger, run_repo, run, event_log_repo):
    _seed_single_run(event_log_repo)
    uc = _uc(logger, run_repo, event_log_repo)

    result = await uc.execute(
        GetRunExecutionLogCommand(run_id="run-1", node_id="a"),
    )
    assert {e["event_type"] for e in result.events} == {"NodeDispatched", "NodeCompleted"}
    assert all((e["payload"] or {}).get("node_id") == "a" for e in result.events)
    assert result.total == 2


@pytest.mark.asyncio
async def test_since_sequence(logger, run_repo, run, event_log_repo):
    _seed_single_run(event_log_repo)
    uc = _uc(logger, run_repo, event_log_repo)

    result = await uc.execute(
        GetRunExecutionLogCommand(run_id="run-1", since_sequence=2),
    )
    assert all(e["sequence"] > 2 for e in result.events)
    assert [e["sequence"] for e in result.events] == [3, 4]
    assert result.total == 2


@pytest.mark.asyncio
async def test_pagination(logger, run_repo, run, event_log_repo):
    _seed_single_run(event_log_repo)
    uc = _uc(logger, run_repo, event_log_repo)

    result = await uc.execute(
        GetRunExecutionLogCommand(run_id="run-1", limit=2, offset=1),
    )
    assert [e["event_type"] for e in result.events] == ["NodeDispatched", "NodeCompleted"]
    assert result.limit == 2
    assert result.offset == 1
    # total is the pre-pagination filtered count.
    assert result.total == 4


@pytest.mark.asyncio
async def test_include_children_merges_and_labels_depth(logger, run_repo, event_log_repo):
    parent = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(child_runs={"subnode": "child-1"}),
    )
    child = WorkflowRun(
        id="child-1",
        workflow_id="wf-child",
        status=RunStatus.COMPLETED,
        parent_run_id="run-1",
        depth_level=1,
        root_run_id="run-1",
    )
    run_repo.save(parent)
    run_repo.save(child)

    # Interleaved timestamps so the merged order alternates parent/child.
    event_log_repo.append(_env("run-1", "RunStatusChanged", ts="2026-05-29T00:00:01Z", event_id="p1", payload={"status": "started"}))
    event_log_repo.append(_env("child-1", "RunStatusChanged", ts="2026-05-29T00:00:02Z", event_id="c1", payload={"status": "started"}))
    event_log_repo.append(_env("run-1", "ChildRunStarted", ts="2026-05-29T00:00:03Z", event_id="p2", payload={"child_run_id": "child-1"}))
    event_log_repo.append(_env("child-1", "NodeCompleted", ts="2026-05-29T00:00:04Z", event_id="c2", payload={"node_id": "x"}))

    uc = _uc(logger, run_repo, event_log_repo)
    result = await uc.execute(GetRunExecutionLogCommand(run_id="run-1"))

    assert [e["run_id"] for e in result.events] == ["run-1", "child-1", "run-1", "child-1"]
    # Child events carry depth 1 relative to the selected run.
    child_depths = {e["depth"] for e in result.events if e["run_id"] == "child-1"}
    assert child_depths == {1}
    assert result.summary["run_ids"] == ["run-1", "child-1"]
    assert result.summary["total_runs"] == 2
    assert result.summary["total_events"] == 4
    # NodeCompleted on the child counts toward completed nodes.
    assert result.summary["node_counts"]["completed"] == 1


@pytest.mark.asyncio
async def test_exclude_children(logger, run_repo, event_log_repo):
    parent = WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(child_runs={"subnode": "child-1"}),
    )
    child = WorkflowRun(id="child-1", workflow_id="wf-child", status=RunStatus.COMPLETED,
                        parent_run_id="run-1", depth_level=1, root_run_id="run-1")
    run_repo.save(parent)
    run_repo.save(child)
    event_log_repo.append(_env("run-1", "RunStatusChanged", ts="2026-05-29T00:00:01Z", event_id="p1", payload={"status": "started"}))
    event_log_repo.append(_env("child-1", "NodeCompleted", ts="2026-05-29T00:00:02Z", event_id="c1", payload={"node_id": "x"}))

    uc = _uc(logger, run_repo, event_log_repo)
    result = await uc.execute(
        GetRunExecutionLogCommand(run_id="run-1", include_children=False),
    )
    assert [e["run_id"] for e in result.events] == ["run-1"]
    assert result.summary["run_ids"] == ["run-1"]
    assert result.summary["total_runs"] == 1


@pytest.mark.asyncio
async def test_empty_log(logger, run_repo, run, event_log_repo):
    uc = _uc(logger, run_repo, event_log_repo)
    result = await uc.execute(GetRunExecutionLogCommand(run_id="run-1"))
    assert result.events == []
    assert result.total == 0
    assert result.summary["status"] == "RUNNING"
    assert result.summary["total_events"] == 0
    assert result.summary["node_counts"] == {"completed": 0, "failed": 0, "skipped": 0}

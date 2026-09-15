"""Tests for MetadataStampingPublisher.

The decorator looks up the run's caller-supplied ``metadata`` (by
``root_run_id`` first, then ``run_id``) and stamps it onto each domain
event so it rides into the realtime + centralized envelopes. Echo-only,
best-effort, and never blocks the inner publish.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.events import RunStatusChanged
from app.layer4_frameworks.messaging.metadata_stamping_publisher import (
    MetadataStampingPublisher,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository


class _CapturingInner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def publish(self, topic: str, event: object) -> None:
        self.calls.append((topic, event))


def _repo_with_run(run: WorkflowRun) -> MemoryRunRepository:
    repo = MemoryRunRepository()
    repo.save(run)
    return repo


def test_stamps_metadata_for_top_level_run():
    meta = {"source_system": "crm", "trace_id": "abc-123"}
    repo = _repo_with_run(WorkflowRun(id="r-1", workflow_id="wf", metadata=meta))
    inner = _CapturingInner()
    pub = MetadataStampingPublisher(inner=inner, run_repo=repo)

    event = RunStatusChanged(run_id="r-1", root_run_id="r-1", status="started")
    pub.publish("run.status", event)

    assert inner.calls[0][1].metadata == meta


def test_child_event_inherits_root_metadata():
    meta = {"tenant_ref": "acme"}
    # Only the ROOT run carries metadata; a child event resolves it by root_run_id.
    repo = _repo_with_run(WorkflowRun(id="root-1", workflow_id="wf", metadata=meta))
    inner = _CapturingInner()
    pub = MetadataStampingPublisher(inner=inner, run_repo=repo)

    event = RunStatusChanged(run_id="child-9", root_run_id="root-1", status="started")
    pub.publish("run.status", event)

    assert inner.calls[0][1].metadata == meta


def test_does_not_overwrite_already_set_metadata():
    repo = _repo_with_run(
        WorkflowRun(id="r-1", workflow_id="wf", metadata={"from": "repo"})
    )
    inner = _CapturingInner()
    pub = MetadataStampingPublisher(inner=inner, run_repo=repo)

    event = RunStatusChanged(run_id="r-1", root_run_id="r-1", status="started")
    event.metadata = {"from": "engine"}  # pre-populated upstream
    pub.publish("run.status", event)

    assert inner.calls[0][1].metadata == {"from": "engine"}


def test_event_with_no_run_id_is_untouched():
    repo = _repo_with_run(WorkflowRun(id="r-1", workflow_id="wf", metadata={"a": 1}))
    inner = _CapturingInner()
    pub = MetadataStampingPublisher(inner=inner, run_repo=repo)

    event = RunStatusChanged(status="started")  # no run_id / root_run_id
    pub.publish("run.status", event)

    assert inner.calls[0][1].metadata == {}


def test_run_with_empty_metadata_leaves_event_empty():
    repo = _repo_with_run(WorkflowRun(id="r-1", workflow_id="wf"))  # metadata={}
    inner = _CapturingInner()
    pub = MetadataStampingPublisher(inner=inner, run_repo=repo)

    event = RunStatusChanged(run_id="r-1", root_run_id="r-1", status="started")
    pub.publish("run.status", event)

    assert inner.calls[0][1].metadata == {}


def test_dict_event_passes_through_untouched():
    repo = _repo_with_run(WorkflowRun(id="r-1", workflow_id="wf", metadata={"a": 1}))
    inner = _CapturingInner()
    pub = MetadataStampingPublisher(inner=inner, run_repo=repo)

    pub.publish("run.status", {"run_id": "r-1", "status": "started"})

    # Inner still invoked; dict event is not mutated with a metadata key.
    assert inner.calls[0][1] == {"run_id": "r-1", "status": "started"}


def test_inner_publish_runs_even_when_lookup_raises():
    class _BoomRepo:
        def get_by_id(self, _id):  # noqa: ANN001
            raise RuntimeError("db down")

    inner = _CapturingInner()
    pub = MetadataStampingPublisher(inner=inner, run_repo=_BoomRepo())

    event = RunStatusChanged(run_id="r-1", root_run_id="r-1", status="started")
    pub.publish("run.status", event)

    assert len(inner.calls) == 1  # never blocked
    assert inner.calls[0][1].metadata == {}


def test_lookup_is_cached_per_run():
    class _CountingRepo:
        def __init__(self) -> None:
            self.calls = 0

        def get_by_id(self, _id):  # noqa: ANN001
            self.calls += 1
            return WorkflowRun(id="r-1", workflow_id="wf", metadata={"a": 1})

    repo = _CountingRepo()
    pub = MetadataStampingPublisher(inner=_CapturingInner(), run_repo=repo)
    for _ in range(3):
        pub.publish("run.status", RunStatusChanged(run_id="r-1", root_run_id="r-1"))

    assert repo.calls == 1

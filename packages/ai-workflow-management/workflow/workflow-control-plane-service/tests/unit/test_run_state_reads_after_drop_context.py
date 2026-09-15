"""Artifacts and skipped-node reads must come from the projector.

Spec items B and C of ``wcp-drop-context-residual-readers-testplan.md``.

* B — ``GET /runs/{id}/artifacts`` read ``run.context.variables["artifacts"]``
  off the reloaded aggregate, so it returned ``{}`` for every artifact ever
  produced. Invisible: an empty panel and a run with no artifacts look the same.
* C — the same for ``skipped_nodes`` on three read endpoints, so a skipped node
  rendered as if it had never run.

Harness invariant: arrange the run with an EMPTY ``RunContext`` and put the
truth in a projector double. A test that writes the field onto ``run.context``
and reads it back tests a round trip HANA no longer performs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.features.get_run_artifacts.use_cases.get_run_artifacts_use_case import (
    GetRunArtifactsCommand,
    GetRunArtifactsUseCase,
)
from app.layer2_application.read.run_state import skipped_nodes_of, variables_of
from tests.conftest import DummyLogger


class ViewDouble:
    def __init__(self, *, variables=None, skipped=None, raises=False, none=False):
        self._variables = variables or {}
        self._skipped = skipped or set()
        self._raises = raises
        self._none = none

    def get(self, run_id: str):
        if self._raises:
            raise RuntimeError("event log unavailable")
        if self._none:
            return None
        return SimpleNamespace(
            variables=dict(self._variables),
            skipped_nodes=set(self._skipped),
        )


# ---------------------------------------------------------------------------
# the helpers — data cases
# ---------------------------------------------------------------------------

class TestSkippedNodesOf:
    def test_reads_the_projector_when_the_aggregate_is_empty(self):
        run = WorkflowRun(id="r", workflow_id="wf", context=RunContext())
        assert skipped_nodes_of("r", ViewDouble(skipped={"n2"}), run) == {"n2"}

    def test_unions_an_in_frame_aggregate_mutation(self):
        """``successor_processor`` un-skips a revisited node synchronously; that
        mutation is younger than the view until the event folds."""
        run = WorkflowRun(id="r", workflow_id="wf",
                          context=RunContext(skipped_nodes={"n3"}))
        assert skipped_nodes_of("r", ViewDouble(skipped={"n2"}), run) == {"n2", "n3"}

    def test_no_view_store_degrades_to_the_aggregate(self):
        run = WorkflowRun(id="r", workflow_id="wf",
                          context=RunContext(skipped_nodes={"n1"}))
        assert skipped_nodes_of("r", None, run) == {"n1"}

    def test_no_run_and_no_view_is_empty(self):
        assert skipped_nodes_of("r", None, None) == set()

    def test_view_returning_none_is_tolerated(self):
        run = WorkflowRun(id="r", workflow_id="wf", context=RunContext())
        assert skipped_nodes_of("r", ViewDouble(none=True), run) == set()

    def test_a_failing_view_store_raises(self):
        """Empty is indistinguishable from 'nothing was skipped', and every
        caller treats it that way — the failure this module exists to end."""
        with pytest.raises(RuntimeError):
            skipped_nodes_of("r", ViewDouble(raises=True), None)


class TestVariablesOf:
    def test_reads_the_projector_when_the_aggregate_is_empty(self):
        run = WorkflowRun(id="r", workflow_id="wf", context=RunContext())
        got = variables_of("r", ViewDouble(variables={"artifacts": {"a": 1}}), run)
        assert got == {"artifacts": {"a": 1}}

    def test_the_view_wins_on_conflict(self):
        """Opposite of ``child_runs_of`` and deliberately so: a variables entry
        is overwritten in place on a re-run, so preferring the aggregate would
        resurrect the previous visit's output."""
        run = WorkflowRun(id="r", workflow_id="wf",
                          context=RunContext(variables={"n1": "OLD"}))
        got = variables_of("r", ViewDouble(variables={"n1": "NEW"}), run)
        assert got["n1"] == "NEW"

    def test_aggregate_only_keys_survive(self):
        """An in-frame seed written before the first publish is not in the view
        yet and must not be dropped."""
        run = WorkflowRun(id="r", workflow_id="wf",
                          context=RunContext(variables={"seeded": 1}))
        got = variables_of("r", ViewDouble(variables={"n1": 2}), run)
        assert got == {"seeded": 1, "n1": 2}

    def test_non_dict_aggregate_is_ignored(self):
        run = SimpleNamespace(context=SimpleNamespace(variables="not-a-dict"))
        assert variables_of("r", ViewDouble(variables={"a": 1}), run) == {"a": 1}

    def test_no_view_store_degrades_to_the_aggregate(self):
        run = WorkflowRun(id="r", workflow_id="wf",
                          context=RunContext(variables={"a": 1}))
        assert variables_of("r", None, run) == {"a": 1}


# ---------------------------------------------------------------------------
# B — the artifacts panel
# ---------------------------------------------------------------------------

class TestGetRunArtifactsFromProjector:
    def _uc(self, run_repo, view):
        return GetRunArtifactsUseCase(
            logger=DummyLogger(), run_repo=run_repo, run_context_view_store=view,
        )

    async def test_artifacts_come_from_the_projector(self, run_repo):
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf",
                                  status=RunStatus.COMPLETED, context=RunContext()))
        registry = {"report": {"file_id": "f-1", "artifact_type": "csv"}}
        result = await self._uc(
            run_repo, ViewDouble(variables={"artifacts": registry}),
        ).execute(GetRunArtifactsCommand(run_id="r1"))

        assert result.error is None
        assert result.artifacts == registry, "artifacts panel was blank"

    async def test_etag_is_stable_across_reads(self, run_repo):
        """The FE caches on it; an etag that moves on every poll defeats that."""
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf", context=RunContext()))
        uc = self._uc(run_repo, ViewDouble(
            variables={"artifacts": {"a": {"file_id": "f-1"}}}))
        first = await uc.execute(GetRunArtifactsCommand(run_id="r1"))
        second = await uc.execute(GetRunArtifactsCommand(run_id="r1"))
        assert first.etag == second.etag
        assert first.etag

    async def test_etag_changes_when_the_registry_changes(self, run_repo):
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf", context=RunContext()))
        a = await self._uc(run_repo, ViewDouble(
            variables={"artifacts": {"a": {"file_id": "f-1"}}})) \
            .execute(GetRunArtifactsCommand(run_id="r1"))
        b = await self._uc(run_repo, ViewDouble(
            variables={"artifacts": {"a": {"file_id": "f-2"}}})) \
            .execute(GetRunArtifactsCommand(run_id="r1"))
        assert a.etag != b.etag

    async def test_no_artifacts_is_still_an_empty_registry(self, run_repo):
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf", context=RunContext()))
        result = await self._uc(run_repo, ViewDouble(variables={"artifacts": {}})) \
            .execute(GetRunArtifactsCommand(run_id="r1"))
        assert result.error is None
        assert result.artifacts == {}

    async def test_a_non_dict_registry_is_refused(self, run_repo):
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf", context=RunContext()))
        result = await self._uc(run_repo, ViewDouble(variables={"artifacts": ["x"]})) \
            .execute(GetRunArtifactsCommand(run_id="r1"))
        assert result.artifacts == {}

    async def test_legacy_bare_string_entries_are_filtered(self, run_repo):
        """Pre-Unit-A entries are invalid for the current contract; a projector
        replaying an old run must not resurrect them into the panel."""
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf", context=RunContext()))
        result = await self._uc(run_repo, ViewDouble(variables={"artifacts": {
            "legacy": "file-123",
            "modern": {"file_id": "f-9", "artifact_type": "csv"},
            "no_file_id": {"artifact_type": "csv"},
        }})).execute(GetRunArtifactsCommand(run_id="r1"))
        assert set(result.artifacts) == {"modern"}

    async def test_missing_run_still_404s(self, run_repo):
        result = await self._uc(run_repo, ViewDouble()) \
            .execute(GetRunArtifactsCommand(run_id="nope"))
        assert result.error_code == "RUN_NOT_FOUND"


# ---------------------------------------------------------------------------
# C — skipped nodes on the read endpoints
# ---------------------------------------------------------------------------

class TestSkippedRendersOnTheReadEndpoints:
    """A skipped node must not render as if it never ran. All three endpoints
    took ``skipped`` off the reloaded aggregate, so all three said NOT_STARTED.
    """

    @staticmethod
    def _workflow():
        from app.layer1_domain.entities.workflow import (
            Workflow, WorkflowEdge, WorkflowNode,
        )
        from app.layer1_domain.value_objects import NodeType

        return Workflow(
            id="wf-1", name="branchy",
            nodes=[
                WorkflowNode(id="cond", name="Cond", node_type=NodeType.CONDITION),
                WorkflowNode(id="taken", name="Taken", node_type=NodeType.TASK,
                             worker_type="w1"),
                WorkflowNode(id="skipped-node", name="Skipped",
                             node_type=NodeType.TASK, worker_type="w1"),
            ],
            edges=[
                WorkflowEdge(source_node_id="cond", target_node_id="taken"),
                WorkflowEdge(source_node_id="cond", target_node_id="skipped-node"),
            ],
        )

    async def test_node_status_reports_skipped_from_the_projector(
        self, workflow_repo, run_repo, task_repo,
    ):
        from app.layer2_application.features.list_run_node_statuses.use_cases.list_run_node_statuses_use_case import (  # noqa: E501
            ListRunNodeStatusesCommand, ListRunNodeStatusesUseCase,
        )
        workflow_repo.save(self._workflow())
        # RUNNING, not COMPLETED: with a terminal run, aggregate_node_status
        # rule 2 ("no tasks + run terminal -> SKIPPED") fires on its own and the
        # assertion below would hold with the bug fully present.
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf-1",
                                  status=RunStatus.RUNNING, context=RunContext()))

        uc = ListRunNodeStatusesUseCase(
            logger=DummyLogger(), run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo,
            run_context_view_store=ViewDouble(skipped={"skipped-node"}),
        )
        result = await uc.execute(ListRunNodeStatusesCommand(run_id="r1"))

        by_id = {n["node_id"]: n["status"] for n in result.nodes}
        assert by_id["skipped-node"] == "SKIPPED", (
            "skipped node rendered as if it never ran"
        )
        # the sibling on the taken branch has no task either — it must NOT be
        # SKIPPED, or the assertion above would pass for the wrong reason
        assert by_id["taken"] == "NOT_STARTED"

    async def test_node_status_without_a_view_store_still_works(
        self, workflow_repo, run_repo, task_repo,
    ):
        """The feature container always injects one, but the use case must not
        hard-depend on it — several harnesses construct it bare."""
        from app.layer2_application.features.list_run_node_statuses.use_cases.list_run_node_statuses_use_case import (  # noqa: E501
            ListRunNodeStatusesCommand, ListRunNodeStatusesUseCase,
        )
        workflow_repo.save(self._workflow())
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf-1", context=RunContext()))
        uc = ListRunNodeStatusesUseCase(
            logger=DummyLogger(), run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo,
        )
        result = await uc.execute(ListRunNodeStatusesCommand(run_id="r1"))
        assert result.error is None
        assert {n["node_id"] for n in result.nodes} == {"cond", "taken", "skipped-node"}

    async def test_get_node_execution_reports_skipped_from_the_projector(
        self, workflow_repo, run_repo, task_repo,
    ):
        from app.layer2_application.features.get_node_execution.use_cases.get_node_execution_use_case import (  # noqa: E501
            GetNodeExecutionCommand, GetNodeExecutionUseCase,
        )
        workflow_repo.save(self._workflow())
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf-1",
                                  status=RunStatus.RUNNING, context=RunContext()))

        uc = GetNodeExecutionUseCase(
            logger=DummyLogger(), run_repo=run_repo, task_repo=task_repo,
            workflow_repo=workflow_repo,
            run_context_view_store=ViewDouble(skipped={"skipped-node"}),
        )
        result = await uc.execute(
            GetNodeExecutionCommand(run_id="r1", node_id="skipped-node"),
        )
        assert result.skipped is True
        assert result.status == "SKIPPED"

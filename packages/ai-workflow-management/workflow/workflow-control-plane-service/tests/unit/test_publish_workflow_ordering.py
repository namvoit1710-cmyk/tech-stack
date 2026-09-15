"""SA-2252 — publish must save the newly-published version LAST.

Regression guard for the "list shows DRAFT right after re-publish" bug. Publishing
demotes the previously-published version to DRAFT and marks the target version
PUBLISHED. "Current row per ID" is resolved by highest REVISION (every save bumps
REVISION), so the target PUBLISHED save MUST run *after* the demote-to-DRAFT save —
otherwise the demoted (now-DRAFT) old version is the most-recently-saved row and the
list/detail resolve the workflow to DRAFT.

pytest.ini sets ``asyncio_mode = auto`` → bare ``async def test_*`` is fine.
"""

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.value_objects.workflow_status import WorkflowStatus
from app.layer2_application.features.publish_workflow.use_cases.publish_workflow_use_case import (
    PublishWorkflowCommand,
    PublishWorkflowUseCase,
)


class _FakeLogger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass


def _wf(version: str, status: WorkflowStatus) -> Workflow:
    return Workflow(
        id="wf-1",
        workflow_id="wf-1",
        name="listen jira",
        version=version,
        status=status,
        nodes=[WorkflowNode(id="n1", name="Start")],  # non-empty → passes _validate_structure
        edges=[],
    )


class _RecordingRepo:
    """Records the order/state of asave() calls; returns v1 as the prior publish."""

    def __init__(self, target: Workflow, prior_published: Workflow):
        self._target = target
        self._prior_published = prior_published
        self.saves: list[tuple[str, str]] = []  # (version, status) in call order

    async def aget_by_id_version(self, workflow_id, version):
        return self._target

    async def aget_list_version_by_status(self, workflow_id, version, status):
        # Mirrors the HANA repo: other PUBLISHED versions (VERSION != target).
        if status == WorkflowStatus.PUBLISHED.value:
            return [self._prior_published]
        return []

    async def asave(self, workflow):
        status = workflow.status.value if isinstance(workflow.status, WorkflowStatus) else workflow.status
        self.saves.append((workflow.version, status))


async def test_publish_saves_target_version_last():
    target = _wf("v2", WorkflowStatus.DRAFT)          # the version being published
    prior = _wf("v1", WorkflowStatus.PUBLISHED)       # the previously-published one
    repo = _RecordingRepo(target, prior)
    use_case = PublishWorkflowUseCase(_FakeLogger(), repo)

    result = await use_case.execute(
        PublishWorkflowCommand(workflow_id="wf-1", version="v2", is_confirm=True)
    )

    assert result.status == "PUBLISHED"
    assert result.version == "v2"

    # Two saves: demote v1 → DRAFT, then publish v2 → PUBLISHED.
    assert repo.saves == [("v1", "DRAFT"), ("v2", "PUBLISHED")]
    # The decisive assertion: the PUBLISHED save is LAST, so it wins highest REVISION.
    assert repo.saves[-1] == ("v2", "PUBLISHED")


async def test_publish_with_no_prior_published_saves_once():
    target = _wf("v1", WorkflowStatus.DRAFT)

    class _NoPriorRepo(_RecordingRepo):
        async def aget_list_version_by_status(self, workflow_id, version, status):
            return []

    repo = _NoPriorRepo(target, target)
    use_case = PublishWorkflowUseCase(_FakeLogger(), repo)

    result = await use_case.execute(
        PublishWorkflowCommand(workflow_id="wf-1", version="v1", is_confirm=True)
    )

    assert result.status == "PUBLISHED"
    assert repo.saves == [("v1", "PUBLISHED")]

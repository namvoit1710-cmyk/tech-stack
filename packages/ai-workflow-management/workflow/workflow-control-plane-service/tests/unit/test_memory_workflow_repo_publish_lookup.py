"""``MemoryWorkflowRepository.get_by_latest_publish_version`` — HANA parity.

The production (HANA) contract: latest PUBLISHED version if one exists,
else latest DRAFT; other statuses are never returned; rows match on the
logical workflow id. The memory repo used to be strict-PUBLISHED keyed on
``workflow_id`` only, which made every DRAFT fixture fail StartRunUseCase
with "Workflow not found".
"""

from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.value_objects.workflow_status import WorkflowStatus
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
    MemoryWorkflowRepository,
)


def _wf(store_id: str, workflow_id: str, version: str, status: WorkflowStatus,
        updated_at: str) -> Workflow:
    return Workflow(
        id=store_id, workflow_id=workflow_id, version=version,
        status=status, name=store_id, updated_at=updated_at,
    )


def test_published_preferred_over_newer_draft():
    repo = MemoryWorkflowRepository()
    repo.save(_wf("w-v1", "w", "1", WorkflowStatus.PUBLISHED, "2026-01-01T00:00:00"))
    repo.save(_wf("w-v2", "w", "2", WorkflowStatus.DRAFT, "2026-02-01T00:00:00"))

    result = repo.get_by_latest_publish_version("w")
    assert result is not None and result.id == "w-v1"


def test_latest_published_wins_among_published():
    repo = MemoryWorkflowRepository()
    repo.save(_wf("w-v1", "w", "1", WorkflowStatus.PUBLISHED, "2026-01-01T00:00:00"))
    repo.save(_wf("w-v2", "w", "2", WorkflowStatus.PUBLISHED, "2026-02-01T00:00:00"))

    result = repo.get_by_latest_publish_version("w")
    assert result is not None and result.id == "w-v2"


def test_falls_back_to_latest_draft():
    repo = MemoryWorkflowRepository()
    repo.save(_wf("w-v1", "w", "1", WorkflowStatus.DRAFT, "2026-01-01T00:00:00"))
    repo.save(_wf("w-v2", "w", "2", WorkflowStatus.DRAFT, "2026-02-01T00:00:00"))

    result = repo.get_by_latest_publish_version("w")
    assert result is not None and result.id == "w-v2"


def test_archived_and_other_statuses_never_returned():
    repo = MemoryWorkflowRepository()
    repo.save(_wf("w-v1", "w", "1", WorkflowStatus.ARCHIVED, "2026-01-01T00:00:00"))

    assert repo.get_by_latest_publish_version("w") is None


def test_matches_on_entity_id_when_workflow_id_defaults():
    """Fixtures construct ``Workflow(id=...)`` and let ``workflow_id``
    default to a uuid — the logical-id match must still find them."""
    repo = MemoryWorkflowRepository()
    wf = Workflow(id="my-wf", version="1", status=WorkflowStatus.DRAFT, name="x")
    repo.save(wf)

    result = repo.get_by_latest_publish_version("my-wf")
    assert result is not None and result.id == "my-wf"


def test_unknown_id_returns_none():
    repo = MemoryWorkflowRepository()
    assert repo.get_by_latest_publish_version("nope") is None

"""SA-892 (task-scoped stop) H1a: update_task is atomic (optimistic lock + retry).

``update_task`` was a blind read-merge-write of the EXECUTIONS blob with NO
optimistic lock, so a racing turn's write could clobber a Stop's ``CANCELLED``
(lost update). It now uses the SAME compare-and-set-on-``UPDATED_AT`` + bounded
retry pattern as ``upsert_executions`` / ``_try_merge_and_update_executions``.
"""

import json
from unittest.mock import MagicMock

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import TaskStatus
from agent_sdk.layer4_frameworks.persistence.hana.base_repository import (
    BaseHanaRepository,
)
from agent_sdk.layer4_frameworks.persistence.hana.hana_agent_shared_state_repository import (
    HanaAgentSharedStateRepository,
)

_STATES_TABLE = '"AIW_AGENT_STATES"'


def _row(status: str = "processing", updated_at: str = "t0") -> dict:
    return {
        "EXECUTIONS": json.dumps(
            [
                {
                    "task_id": "s1",
                    "agent": "agent.file",
                    "name": "Parse file",
                    "status": status,
                    "message_id": "m1",
                },
                {
                    "task_id": "s2",
                    "agent": "agent.validate",
                    "name": "Validate",
                    "status": "pending",
                    "message_id": "m1",
                },
            ]
        ),
        "UPDATED_AT": updated_at,
    }


def _repo(db: MagicMock) -> HanaAgentSharedStateRepository:
    return HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))


def test_update_task_writes_with_cas_on_updated_at():
    db = MagicMock()
    db.execute_query.return_value = [_row(updated_at="t0")]
    db.execute_write.return_value = 1
    repo = _repo(db)

    result = repo.update_task("conv-1", "s1", status=TaskStatus.CANCELLED)

    assert result is not None
    assert result.status == TaskStatus.CANCELLED
    sql, params = db.execute_write.call_args.args
    # Optimistic lock: the UPDATE is conditioned on the row's UPDATED_AT.
    assert 'AND "UPDATED_AT" = :p3' in sql
    assert params[2] == "conv-1"
    assert params[3] == "t0"
    # Only the target task's status changed; the sibling is untouched.
    written = json.loads(params[0])
    s1 = next(t for t in written if t["task_id"] == "s1")
    s2 = next(t for t in written if t["task_id"] == "s2")
    assert s1["status"] == "cancelled"
    assert s2["status"] == "pending"


def test_update_task_retries_on_cas_conflict_no_lost_update():
    """A racing write bumps UPDATED_AT: the first CAS loses (0 rows), so the repo
    re-reads and re-applies the merge against the fresh row, and the second write
    wins. The update is not lost."""
    db = MagicMock()
    db.execute_query.side_effect = [
        [_row(updated_at="t0")],  # first read
        [_row(updated_at="t1")],  # re-read after the conflict (row changed)
    ]
    db.execute_write.side_effect = [0, 1]  # CAS conflict, then success
    repo = _repo(db)

    result = repo.update_task("conv-1", "s1", status=TaskStatus.CANCELLED)

    assert result is not None
    assert result.status == TaskStatus.CANCELLED
    assert db.execute_write.call_count == 2  # retried after conflict
    assert db.execute_query.call_count == 2  # re-read before retry
    # The winning write CASes against the freshly re-read UPDATED_AT.
    second_params = db.execute_write.call_args_list[1].args[1]
    assert second_params[3] == "t1"


def test_update_task_exhausts_retries_raises_high_concurrency():
    db = MagicMock()
    db.execute_query.return_value = [_row()]
    db.execute_write.return_value = 0  # CAS always loses
    repo = _repo(db)

    with pytest.raises(Exception, match="high concurrency"):
        repo.update_task("conv-1", "s1", status=TaskStatus.CANCELLED)
    assert db.execute_write.call_count == 3  # bounded retry (max_retries=3)


def test_update_task_missing_task_raises_valueerror():
    db = MagicMock()
    db.execute_query.return_value = [_row()]
    repo = _repo(db)

    with pytest.raises(ValueError, match="not found"):
        repo.update_task("conv-1", "does-not-exist", status=TaskStatus.CANCELLED)
    # Deterministic — no retry, no write.
    db.execute_write.assert_not_called()


def test_update_task_no_row_raises_valueerror():
    db = MagicMock()
    db.execute_query.return_value = []
    repo = _repo(db)

    with pytest.raises(ValueError, match="not found"):
        repo.update_task("conv-1", "s1", status=TaskStatus.CANCELLED)


def test_update_task_unknown_field_raises():
    db = MagicMock()
    repo = _repo(db)

    with pytest.raises(ValueError, match="Unknown Task fields"):
        repo.update_task("conv-1", "s1", bogus="x")

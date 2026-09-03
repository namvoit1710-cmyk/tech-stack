from unittest.mock import MagicMock


def test_get_returns_shared_state_record_with_lock_metadata():
    from agent_sdk.layer1_domain.entities.shared_state import SharedStateRecord
    from agent_sdk.layer4_frameworks.persistence.hana.hana_shared_state_repository import (
        HanaSharedStateRepository,
    )

    db = MagicMock()
    db.execute_query.return_value = [
        {
            "state_key": "workflow-1",
            "state_data": '{"steps": ["draft"]}',
            "version": 4,
            "updated_at": "2026-01-01T00:00:00+00:00",
            "lock_owner": "agent-1",
            "lock_acquired_at": "2026-01-01T00:00:00+00:00",
            "lock_expires_at": "2026-01-01T00:05:00+00:00",
        }
    ]

    repository = HanaSharedStateRepository(db=db)

    record = repository.get("workflow-1")

    assert isinstance(record, SharedStateRecord)
    assert record.key == "workflow-1"
    assert record.state == {"steps": ["draft"]}
    assert record.version == 4
    assert record.lock is not None
    assert record.lock.owner == "agent-1"


def test_get_reads_lob_state_data_before_json_parsing():
    from agent_sdk.layer4_frameworks.persistence.hana.hana_shared_state_repository import (
        HanaSharedStateRepository,
    )

    class _FakeLob:
        def __init__(self, raw_value):
            self._raw_value = raw_value

        def read(self):
            return self._raw_value

    db = MagicMock()
    db.execute_query.return_value = [
        {
            "state_key": "workflow-1",
            "state_data": _FakeLob('{"steps": ["draft"]}'),
            "version": 4,
            "updated_at": "2026-01-01T00:00:00+00:00",
            "lock_owner": None,
            "lock_acquired_at": None,
            "lock_expires_at": None,
        }
    ]

    repository = HanaSharedStateRepository(db=db)

    record = repository.get("workflow-1")

    assert record is not None
    assert record.state == {"steps": ["draft"]}


def test_save_upserts_shared_state_record():
    from agent_sdk.layer1_domain.entities.shared_state import SharedStateRecord
    from agent_sdk.layer4_frameworks.persistence.hana.hana_shared_state_repository import (
        HanaSharedStateRepository,
    )

    db = MagicMock()
    repository = HanaSharedStateRepository(db=db)
    record = SharedStateRecord(
        key="workflow-1",
        state={"steps": ["draft"]},
        version=2,
        updated_at="2026-01-01T00:00:00+00:00",
    )

    saved = repository.save(record)

    assert saved == record
    sql, params = db.execute_write.call_args.args
    assert 'MERGE INTO "AIW_SHARED_STATES"' in sql
    assert params[0] == "workflow-1"
    assert params[2] == 2


def test_compare_and_set_returns_incremented_record_when_version_matches():
    from agent_sdk.layer4_frameworks.persistence.hana.hana_shared_state_repository import (
        HanaSharedStateRepository,
    )

    db = MagicMock()
    db.execute_write.return_value = 1
    repository = HanaSharedStateRepository(db=db)

    updated = repository.compare_and_set(
        key="workflow-1",
        state={"steps": ["review"]},
        expected_version=2,
    )

    assert updated is not None
    assert updated.key == "workflow-1"
    assert updated.state == {"steps": ["review"]}
    assert updated.version == 3
    sql, params = db.execute_write.call_args.args
    assert 'target."VERSION" = src."EXPECTED_VERSION"' in sql
    assert params[0] == "workflow-1"
    assert params[1] == 2


def test_acquire_lock_creates_lock_metadata_for_owner():
    from agent_sdk.layer4_frameworks.persistence.hana.hana_shared_state_repository import (
        HanaSharedStateRepository,
    )

    db = MagicMock()
    db.execute_write.return_value = 1
    repository = HanaSharedStateRepository(db=db)

    locked_record = repository.acquire_lock(
        key="workflow-1",
        owner="agent-1",
        ttl_seconds=120,
    )

    assert locked_record is not None
    assert locked_record.key == "workflow-1"
    assert locked_record.version == 0
    assert locked_record.lock is not None
    assert locked_record.lock.owner == "agent-1"
    sql, params = db.execute_write.call_args.args
    assert 'MERGE INTO "AIW_SHARED_STATES"' in sql
    assert params[0] == "workflow-1"
    assert params[1] == "agent-1"


def test_acquire_lock_returns_persisted_existing_record_state_and_version():
    from agent_sdk.layer4_frameworks.persistence.hana.hana_shared_state_repository import (
        HanaSharedStateRepository,
    )

    db = MagicMock()
    db.execute_write.return_value = 1
    db.execute_query.return_value = [
        {
            "state_key": "workflow-1",
            "state_data": '{"steps": ["draft"], "approved": true}',
            "version": 4,
            "updated_at": "2026-01-01T00:01:00+00:00",
            "lock_owner": "agent-1",
            "lock_acquired_at": "2026-01-01T00:01:00+00:00",
            "lock_expires_at": "2026-01-01T00:03:00+00:00",
        }
    ]
    repository = HanaSharedStateRepository(db=db)

    locked_record = repository.acquire_lock(
        key="workflow-1",
        owner="agent-1",
        ttl_seconds=120,
    )

    assert locked_record is not None
    assert locked_record.state == {"steps": ["draft"], "approved": True}
    assert locked_record.version == 4
    assert locked_record.updated_at == "2026-01-01T00:01:00+00:00"
    assert locked_record.lock is not None
    assert locked_record.lock.owner == "agent-1"
    db.execute_query.assert_called_once()


def test_release_lock_only_succeeds_for_matching_owner():
    from agent_sdk.layer4_frameworks.persistence.hana.hana_shared_state_repository import (
        HanaSharedStateRepository,
    )

    db = MagicMock()
    db.execute_write.return_value = 1
    repository = HanaSharedStateRepository(db=db)

    released = repository.release_lock("workflow-1", "agent-1")

    assert released is True
    sql, params = db.execute_write.call_args.args
    assert 'UPDATE "AIW_SHARED_STATES"' in sql
    assert params[0] == "workflow-1"
    assert params[1] == "agent-1"

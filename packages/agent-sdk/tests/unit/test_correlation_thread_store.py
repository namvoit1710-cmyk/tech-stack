from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_sdk.layer1_domain.entities.shared_state import SharedStateRecord


class _StubSharedStateRepository:
    def __init__(self) -> None:
        self.records: dict[str, SharedStateRecord] = {}
        self.deleted: list[str] = []

    def load(self, key: str) -> SharedStateRecord | None:
        return self.records.get(key)

    def get(self, key: str) -> SharedStateRecord | None:
        return self.records.get(key)

    def save(self, record: SharedStateRecord) -> SharedStateRecord:
        self.records[record.key] = record
        return record

    def compare_and_set(
        self,
        key: str,
        state: dict,
        expected_version: int,
    ) -> SharedStateRecord | None:
        current = self.records.get(key)
        if current is None or current.version != expected_version:
            return None
        updated = SharedStateRecord(
            key=key,
            state=state,
            version=expected_version + 1,
            updated_at=current.updated_at,
        )
        self.records[key] = updated
        return updated

    def acquire_lock(
        self, key: str, owner: str, ttl_seconds: int
    ) -> SharedStateRecord | None:
        return self.records.get(key)

    def release_lock(self, key: str, owner: str) -> bool:
        return True

    def delete(self, key: str) -> bool:
        self.deleted.append(key)
        return self.records.pop(key, None) is not None


def _iso_at(offset_seconds: int) -> str:
    return (
        datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)
    ).isoformat()


def test_store_round_trips_persistent_correlation_thread_mapping():
    from agent_sdk.layer2_application.services.correlation_thread_store import (
        CorrelationThreadStore,
    )

    repo = _StubSharedStateRepository()
    store = CorrelationThreadStore(
        shared_state_repository=repo,
        now=lambda: datetime.fromisoformat(_iso_at(0)),
    )

    store.remember("corr-1", "thread-1")

    assert store.resolve("corr-1") == "thread-1"
    assert repo.records["agent-call-correlation:corr-1"].state == {
        "thread_id": "thread-1",
        "stored_at": _iso_at(0),
    }


def test_store_deletes_expired_persistent_correlation_mapping():
    from agent_sdk.layer2_application.services.correlation_thread_store import (
        CorrelationThreadStore,
    )

    repo = _StubSharedStateRepository()
    repo.save(
        SharedStateRecord(
            key="agent-call-correlation:corr-expired",
            state={"thread_id": "thread-x", "stored_at": _iso_at(0)},
            version=1,
            updated_at=_iso_at(0),
        )
    )
    store = CorrelationThreadStore(
        shared_state_repository=repo,
        ttl_seconds=5,
        now=lambda: datetime.fromisoformat(_iso_at(10)),
    )

    assert store.resolve("corr-expired") is None
    assert repo.deleted == ["agent-call-correlation:corr-expired"]


def test_store_evicts_oldest_fallback_entries_when_limit_is_exceeded():
    from agent_sdk.layer2_application.services.correlation_thread_store import (
        CorrelationThreadStore,
    )

    fallback_threads: dict[str, str] = {}
    current_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def _now() -> datetime:
        return current_time

    store = CorrelationThreadStore(
        fallback_threads=fallback_threads,
        ttl_seconds=3600,
        max_entries=2,
        now=_now,
    )

    store.remember("corr-1", "thread-1")
    current_time += timedelta(seconds=1)
    store.remember("corr-2", "thread-2")
    current_time += timedelta(seconds=1)
    store.remember("corr-3", "thread-3")

    assert fallback_threads == {
        "corr-2": "thread-2",
        "corr-3": "thread-3",
    }

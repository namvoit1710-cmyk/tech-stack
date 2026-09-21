from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, cast

from agent_sdk.layer1_domain.entities.shared_state import SharedStateRecord
from agent_sdk.layer2_application.interfaces.shared_state_repository import (
    ISharedStateRepository,
)

_CORRELATION_KEY_PREFIX = "agent-call-correlation:"


def _coerce_non_negative_int(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(value, 0)
    if isinstance(value, str):
        try:
            return max(int(value.strip()), 0)
        except (TypeError, ValueError):
            return default
    return default


class CorrelationThreadStore:
    def __init__(
        self,
        *,
        shared_state_repository: ISharedStateRepository | None = None,
        fallback_threads: dict[str, str] | None = None,
        ttl_seconds: int = 3600,
        max_entries: int = 10000,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._shared_state_repository = shared_state_repository
        self._fallback_threads = (
            fallback_threads if fallback_threads is not None else {}
        )
        self._ttl_seconds = _coerce_non_negative_int(ttl_seconds, 3600)
        self._max_entries = max(_coerce_non_negative_int(max_entries, 10000), 1)
        self._now = now or self._utcnow
        current_timestamp = self._now().isoformat()
        self._fallback_stored_at = {
            correlation_id: current_timestamp
            for correlation_id in self._fallback_threads.keys()
        }

    def remember(self, correlation_id: str, thread_id: str) -> None:
        if not correlation_id or not thread_id:
            return

        stored_at = self._now().isoformat()
        record_key = self._build_key(correlation_id)
        repository = self._shared_state_repository
        if repository is not None:
            existing = repository.load(record_key)
            version = (existing.version + 1) if existing is not None else 0
            repository.save(
                SharedStateRecord(
                    key=record_key,
                    state={"thread_id": thread_id, "stored_at": stored_at},
                    version=version,
                    updated_at=stored_at,
                )
            )
            return

        self._cleanup_fallback_expired(now=self._now())
        if correlation_id in self._fallback_threads:
            self._fallback_threads.pop(correlation_id, None)
            self._fallback_stored_at.pop(correlation_id, None)
        self._fallback_threads[correlation_id] = thread_id
        self._fallback_stored_at[correlation_id] = stored_at
        self._enforce_fallback_limit()

    def resolve(self, correlation_id: str) -> str | None:
        if not correlation_id:
            return None

        record_key = self._build_key(correlation_id)
        repository = self._shared_state_repository
        if repository is not None:
            record = repository.load(record_key)
            if record is None:
                return None
            stored_at = self._coerce_stored_at(record.state)
            if stored_at is None or self._is_expired(stored_at, now=self._now()):
                self._delete_persistent_record(repository, record_key)
                return None
            thread_id = (
                record.state.get("thread_id")
                if isinstance(record.state, dict)
                else None
            )
            return thread_id if isinstance(thread_id, str) and thread_id else None

        self._cleanup_fallback_expired(now=self._now())
        return self._fallback_threads.get(correlation_id)

    def forget(self, correlation_id: str) -> None:
        if not correlation_id:
            return

        record_key = self._build_key(correlation_id)
        repository = self._shared_state_repository
        if repository is not None:
            self._delete_persistent_record(repository, record_key)
        self._fallback_threads.pop(correlation_id, None)
        self._fallback_stored_at.pop(correlation_id, None)

    @staticmethod
    def _build_key(correlation_id: str) -> str:
        return f"{_CORRELATION_KEY_PREFIX}{correlation_id}"

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def _cleanup_fallback_expired(self, *, now: datetime) -> None:
        expired_ids = [
            correlation_id
            for correlation_id, stored_at in self._fallback_stored_at.items()
            if self._is_expired(stored_at, now=now)
        ]
        for correlation_id in expired_ids:
            self._fallback_threads.pop(correlation_id, None)
            self._fallback_stored_at.pop(correlation_id, None)

    def _enforce_fallback_limit(self) -> None:
        while len(self._fallback_threads) > self._max_entries:
            oldest_correlation_id = next(iter(self._fallback_threads))
            self._fallback_threads.pop(oldest_correlation_id, None)
            self._fallback_stored_at.pop(oldest_correlation_id, None)

    def _is_expired(self, stored_at: str, *, now: datetime) -> bool:
        if self._ttl_seconds == 0:
            return False
        parsed = datetime.fromisoformat(stored_at)
        return parsed + timedelta(seconds=self._ttl_seconds) <= now

    @staticmethod
    def _coerce_stored_at(state: object) -> str | None:
        if not isinstance(state, dict):
            return None
        state_dict = cast(dict[str, Any], state)
        stored_at = state_dict.get("stored_at")
        return stored_at if isinstance(stored_at, str) and stored_at else None

    @staticmethod
    def _delete_persistent_record(
        repository: ISharedStateRepository,
        record_key: str,
    ) -> None:
        delete = getattr(repository, "delete", None)
        if callable(delete):
            delete(record_key)

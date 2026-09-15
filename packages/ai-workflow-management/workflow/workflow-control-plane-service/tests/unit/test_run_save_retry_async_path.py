"""``save_run_with_cas_retry`` async-path routing.

Verifies the helper prefers ``asave`` / ``aget_by_id`` when they exist
as real coroutine functions, and falls back to sync ``save`` /
``get_by_id`` otherwise. The introspection guard matters because
``MagicMock`` auto-creates non-coroutine attributes for any name; the
helper must not naively ``await`` those.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.repositories.run_repository_interface import (
    OptimisticConcurrencyError,
)
from app.layer2_application.run_save_retry import save_run_with_cas_retry
from app.layer4_frameworks.persistence.memory.memory_run_repo import (
    MemoryRunRepository,
)


def _seed_run(repo: MemoryRunRepository, run_id: str = "r-1") -> WorkflowRun:
    run = WorkflowRun(id=run_id, workflow_id="wf-1", status=RunStatus.RUNNING)
    repo.save(run)  # v=1
    return run


class TestAsyncPathRouting:
    @pytest.mark.asyncio
    async def test_helper_routes_through_async_methods_when_available(self):
        """When the repo exposes coroutine-function ``asave`` /
        ``aget_by_id``, the helper invokes those — not the sync
        siblings. Use a fake repo where the async methods produce a
        different observable side effect than the sync ones."""

        class _DivergentRepo:
            """Async and sync siblings touch separate counters so we
            can prove which one the helper chose. A real Memory/Hana
            repo wouldn't behave this way — this fake just isolates
            the routing decision."""

            def __init__(self) -> None:
                self.run = WorkflowRun(
                    id="r-1", workflow_id="wf-1",
                    status=RunStatus.RUNNING, version=1,
                )
                self.async_calls = 0
                self.sync_calls = 0

            def get_by_id(self, rid):
                self.sync_calls += 1
                return self.run if rid == "r-1" else None

            def save(self, run):
                self.sync_calls += 1
                run.version += 1

            async def aget_by_id(self, rid):
                self.async_calls += 1
                return self.run if rid == "r-1" else None

            async def asave(self, run):
                self.async_calls += 1
                run.version += 1

        repo = _DivergentRepo()

        def _apply(r):
            r.completed_at = "2026-05-27T00:00:00+00:00"

        await save_run_with_cas_retry(repo, "r-1", _apply)

        # Helper used the async path exclusively.
        assert repo.async_calls == 2  # aget_by_id + asave
        assert repo.sync_calls == 0

    @pytest.mark.asyncio
    async def test_helper_falls_back_to_sync_when_async_methods_absent(self):
        """A legacy repo without ``asave``/``aget_by_id`` should still
        work — the helper falls back to sync ``save``/``get_by_id``."""

        class _SyncOnlyRepo:
            def __init__(self) -> None:
                self.run = WorkflowRun(
                    id="r-1", workflow_id="wf-1",
                    status=RunStatus.RUNNING, version=1,
                )
                self.save_calls = 0
                self.get_calls = 0

            def get_by_id(self, rid):
                self.get_calls += 1
                return self.run if rid == "r-1" else None

            def save(self, run):
                self.save_calls += 1
                run.version += 1

        repo = _SyncOnlyRepo()

        def _apply(r):
            r.completed_at = "2026-05-27T00:00:00+00:00"

        result = await save_run_with_cas_retry(repo, "r-1", _apply)

        assert result is not None
        assert repo.save_calls == 1
        assert repo.get_calls == 1

    @pytest.mark.asyncio
    async def test_helper_ignores_magicmock_style_attributes(self):
        """``unittest.mock.MagicMock`` auto-creates attribute access,
        so ``hasattr(repo, "asave")`` would be True even when the test
        author never wired the method. The helper guards against this
        by checking ``iscoroutinefunction``."""
        from unittest.mock import MagicMock

        repo = MagicMock()
        run = WorkflowRun(
            id="r-1", workflow_id="wf-1",
            status=RunStatus.RUNNING, version=1,
        )
        repo.get_by_id.return_value = run
        repo.save.return_value = None

        def _apply(r):
            r.completed_at = "2026-05-27T00:00:00+00:00"

        # No TypeError ``object MagicMock can't be used in 'await' expression``
        await save_run_with_cas_retry(repo, "r-1", _apply)

        # Sync methods invoked exactly once (no retry).
        repo.get_by_id.assert_called_once_with("r-1")
        repo.save.assert_called_once_with(run)


class TestAsyncPathPreservesConflictPropagation:
    @pytest.mark.asyncio
    async def test_async_path_retries_on_conflict(self):
        """When the async ``asave`` raises OCE on the first attempt,
        the helper retries via ``aget_by_id`` + ``asave`` — same
        semantics as the sync path."""
        repo = MemoryRunRepository()
        _seed_run(repo)

        call_count = {"asave": 0}
        orig_asave = repo.asave

        async def _intermittent_asave(run):
            call_count["asave"] += 1
            if call_count["asave"] == 1:
                raise OptimisticConcurrencyError(
                    run.id, expected=run.version, actual=run.version + 1,
                )
            return await orig_asave(run)

        repo.asave = _intermittent_asave  # type: ignore[assignment]

        def _apply(r):
            r.completed_at = "2026-05-27T00:00:00+00:00"

        result = await save_run_with_cas_retry(repo, "r-1", _apply)

        assert result is not None
        # Helper retried — 2 asave attempts (first conflict, second success).
        assert call_count["asave"] == 2

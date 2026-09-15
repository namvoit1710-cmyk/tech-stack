"""``AsyncRepoMixin`` auto-generates async wrappers via ``__getattr__``.

Covers both flavors:
- ``hana.async_repo_mixin.AsyncRepoMixin`` delegates through
  ``self._db.arun`` (executor offload)
- ``memory.async_repo_mixin.AsyncRepoMixin`` just calls the sync
  method inside an ``async def`` (no executor, since memory is
  microseconds)

Both produce real ``coroutine functions`` so the helper's
``inspect.iscoroutinefunction`` guard treats them as async-capable.
"""
from __future__ import annotations

import inspect

import pytest

from app.layer4_frameworks.persistence.hana.async_repo_mixin import (
    AsyncRepoMixin as HanaAsyncRepoMixin,
)
from app.layer4_frameworks.persistence.memory.async_repo_mixin import (
    AsyncRepoMixin as MemoryAsyncRepoMixin,
)


# ───────────────────────── HANA flavor ──────────────────────────────


class _FakeHanaDb:
    """Mimics the relevant ``HanaConnectionManager.arun`` surface."""

    def __init__(self) -> None:
        self.calls: list = []

    async def arun(self, fn, *args, **kwargs):
        self.calls.append((fn.__name__, args, kwargs))
        return fn(*args, **kwargs)


class _FakeHanaRepo(HanaAsyncRepoMixin):
    def __init__(self) -> None:
        self._db = _FakeHanaDb()
        self.sync_calls = 0

    def save(self, x):
        self.sync_calls += 1
        return ("saved", x)

    def get_by_id(self, x):
        self.sync_calls += 1
        return ("got", x)


class TestHanaMixin:
    @pytest.mark.asyncio
    async def test_generates_async_wrapper_for_save(self):
        repo = _FakeHanaRepo()
        assert hasattr(repo, "asave")
        assert inspect.iscoroutinefunction(repo.asave)

        result = await repo.asave("payload-1")
        assert result == ("saved", "payload-1")
        # Routed through arun → executor delegation.
        assert repo._db.calls == [("save", ("payload-1",), {})]
        assert repo.sync_calls == 1

    @pytest.mark.asyncio
    async def test_generates_async_wrapper_for_get_by_id(self):
        repo = _FakeHanaRepo()
        result = await repo.aget_by_id("r-1")
        assert result == ("got", "r-1")

    def test_unknown_async_method_raises_attribute_error(self):
        repo = _FakeHanaRepo()
        with pytest.raises(AttributeError):
            repo.anonexistent  # noqa: B018

    def test_non_a_prefixed_attributes_still_raise(self):
        repo = _FakeHanaRepo()
        with pytest.raises(AttributeError):
            repo.bogus  # noqa: B018


# ───────────────────────── Memory flavor ───────────────────────────


class _FakeMemoryRepo(MemoryAsyncRepoMixin):
    def __init__(self) -> None:
        self.sync_calls = 0

    def save(self, x):
        self.sync_calls += 1
        return ("saved", x)

    def get_by_id(self, x):
        self.sync_calls += 1
        return ("got", x)


class TestMemoryMixin:
    @pytest.mark.asyncio
    async def test_generates_async_wrapper_for_save(self):
        repo = _FakeMemoryRepo()
        assert hasattr(repo, "asave")
        assert inspect.iscoroutinefunction(repo.asave)

        result = await repo.asave("payload-1")
        assert result == ("saved", "payload-1")
        # Memory mixin skips ``arun`` (no executor) — sync method called
        # directly.
        assert repo.sync_calls == 1

    def test_unknown_async_method_raises_attribute_error(self):
        repo = _FakeMemoryRepo()
        with pytest.raises(AttributeError):
            repo.anonexistent  # noqa: B018


# ───────────────────────── real-world smoke test ───────────────────


class TestRealReposHaveAsyncVariants:
    """Sanity check that the mixin is wired on the actual production
    repo classes — the wide application during #17. If a future change
    drops the inheritance on any of these, this test catches it."""

    def test_hana_task_repo_exposes_asave(self):
        from unittest.mock import MagicMock
        from app.layer4_frameworks.persistence.hana.hana_task_repo import (
            HanaTaskRepository,
        )
        db = MagicMock()
        db.arun = lambda fn, *a, **kw: None  # not a coroutine; we only check shape
        repo = HanaTaskRepository(db)
        assert hasattr(repo, "asave")
        assert hasattr(repo, "aget_by_id")
        assert hasattr(repo, "alist_by_run")

    def test_hana_workflow_repo_exposes_aget_by_id(self):
        from unittest.mock import MagicMock
        from app.layer4_frameworks.persistence.hana.hana_workflow_repo import (
            HanaWorkflowRepository,
        )
        db = MagicMock()
        db.arun = lambda fn, *a, **kw: None
        repo = HanaWorkflowRepository(db)
        assert hasattr(repo, "asave")
        assert hasattr(repo, "aget_by_id")
        assert hasattr(repo, "alist")

    def test_memory_task_repo_exposes_asave(self):
        from app.layer4_frameworks.persistence.memory.memory_task_repo import (
            MemoryTaskRepository,
        )
        repo = MemoryTaskRepository()
        assert hasattr(repo, "asave")
        assert hasattr(repo, "aget_by_id")
        assert hasattr(repo, "alist_by_run")

    def test_memory_workflow_repo_exposes_async_methods(self):
        from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
            MemoryWorkflowRepository,
        )
        repo = MemoryWorkflowRepository()
        assert hasattr(repo, "asave")
        assert hasattr(repo, "aget_by_id")

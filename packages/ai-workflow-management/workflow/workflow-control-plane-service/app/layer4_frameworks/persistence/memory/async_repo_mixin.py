"""Async-method auto-generation for in-memory repositories.

The HANA version (``hana.async_repo_mixin.AsyncRepoMixin``) delegates
to ``self._db.arun`` so the sync call runs on the connection manager's
dedicated executor.

Memory repos don't have a ``_db``; their sync methods are already
microsecond-fast in-memory operations. The async wrapper just calls
the sync method inside an ``async def`` so it satisfies the same
interface and the call-site ``await`` works.
"""
from __future__ import annotations

from typing import Any


class AsyncRepoMixin:
    """Mix into memory repos to expose ``a<method>`` variants. The
    wrapper just calls the sync method directly — memory ops don't
    benefit from executor offload."""

    def __getattr__(self, name: str) -> Any:
        if not name.startswith("a") or name.startswith("__") or len(name) < 2:
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}"
            )
        sync_name = name[1:]
        cls = type(self)
        sync_method = None
        for klass in cls.__mro__:
            if sync_name in klass.__dict__:
                candidate = klass.__dict__[sync_name]
                if callable(candidate):
                    sync_method = candidate
                break
        if sync_method is None:
            raise AttributeError(
                f"{cls.__name__!r} object has no attribute {name!r} "
                f"(no sync method {sync_name!r} to wrap)"
            )

        bound_sync = getattr(self, sync_name)

        async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return bound_sync(*args, **kwargs)

        _async_wrapper.__name__ = name
        _async_wrapper.__qualname__ = f"{cls.__name__}.{name}"
        return _async_wrapper

"""Async-method auto-generation for HANA repositories.

Every HANA repo currently exposes a sync API (``save``, ``get_by_id``,
etc.) that internally calls ``self._db.execute_*``. Under Part 2 of
the async-I/O fix we also need async variants of every method that
offload to the connection manager's dedicated executor.

Hand-writing each variant is ~30+ near-identical declarations. This
mixin generates them on demand via ``__getattr__``: any attribute
access starting with ``a`` (e.g. ``asave``, ``aget_by_id``,
``alist_by_run``) is rewritten to ``await self._db.arun(self.X, ...)``
where ``X`` is the name with the leading ``a`` stripped.

Repos opt in by inheriting ``AsyncRepoMixin`` and ensuring ``self._db``
exposes ``arun(fn, *args)``. Callers that explicitly check for a method
via ``hasattr(repo, "asave")`` will see ``True`` once the mixin is on
the class — match the helper's ``inspect.iscoroutinefunction`` guard
to filter mocks.
"""
from __future__ import annotations

from typing import Any


class AsyncRepoMixin:
    """Mix into HANA repos to expose ``a<method>`` variants of all
    sync methods. Requires ``self._db.arun(fn, *args, **kwargs)``.

    Example: ``HanaTaskRepository(AsyncRepoMixin, ITaskRepository)``
    gives ``await repo.asave(task)`` for free, delegating through
    the connection manager's executor.
    """

    # Cache of generated wrappers per sync method name. Avoids
    # rebuilding the closure on every attribute access.
    _async_wrapper_cache: dict[str, Any]

    def __getattr__(self, name: str) -> Any:
        # ``__getattr__`` is only called when normal lookup fails, so
        # this never shadows an explicitly-defined async method.
        if not name.startswith("a") or name.startswith("__") or len(name) < 2:
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}"
            )
        sync_name = name[1:]
        # Look up the sync method on the class (avoids recursion into
        # __getattr__) — must be callable to be wrappable.
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

        db = self.__dict__.get("_db")
        if db is None or not hasattr(db, "arun"):
            raise AttributeError(
                f"{cls.__name__!r} cannot expose {name!r}: ``self._db.arun`` "
                f"not available"
            )

        bound_sync = getattr(self, sync_name)

        async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await db.arun(bound_sync, *args, **kwargs)

        # Bind a coroutine-function so ``inspect.iscoroutinefunction``
        # returns True on the result (matches helper's MagicMock guard).
        _async_wrapper.__name__ = name
        _async_wrapper.__qualname__ = f"{cls.__name__}.{name}"
        return _async_wrapper

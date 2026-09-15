"""Round-robin selection for list-type environment variables (SA-2372).

A ``list`` env variable is a pool of values (e.g. the claude API-token list).
When bound to a run, the pool is exposed as::

    {{$env.CLAUDE_TOKENS.all}}      -> the whole list
    {{$env.CLAUDE_TOKENS.current}}  -> one value, chosen round-robin per run
    {{$env.CLAUDE_TOKENS.index}}    -> the 0-based index of ``current``
    {{$env.CLAUDE_TOKENS.count}}    -> pool size

Round-robin state lives in an in-process cursor registry keyed by
``tenant:environment_id:variable_key``. This is best-effort: cursors are
per-process (not shared across pods) and reset on restart — good enough to
spread load across a token pool; a durable cursor is a follow-up.
"""
from __future__ import annotations

from typing import Any


class PoolCursorRegistry:
    """In-memory round-robin cursors. One instance per StartRunUseCase."""

    def __init__(self) -> None:
        self._cursors: dict[str, int] = {}

    def select(self, cursor_key: str, values: list[Any]) -> tuple[Any, int]:
        """Return ``(value, index)`` for the next round-robin pick and advance."""
        if not values:
            return None, -1
        i = self._cursors.get(cursor_key, 0) % len(values)
        self._cursors[cursor_key] = (i + 1) % len(values)
        return values[i], i


def expose_pool(values: list[Any], current: Any, index: int) -> dict[str, Any]:
    """Structured projection for a list variable in the ``$env`` source."""
    return {
        "all": list(values),
        "current": current,
        "index": index,
        "count": len(values),
    }


def build_env_source(env: Any, cursors: PoolCursorRegistry) -> dict[str, Any]:
    """Build the ``$env`` map for a run from an Environment entity.

    Non-secret variables become their value; list (pool) variables become a
    round-robin structured object (``{all, current, index, count}``). Secret
    (credential-type) variables are excluded by ``non_secret_env_map``.
    """
    env_map = env.non_secret_env_map()
    for key, value in list(env_map.items()):
        if isinstance(value, list):
            cursor_key = f"{env.tenant_id}:{env.id}:{key}"
            current, index = cursors.select(cursor_key, value)
            env_map[key] = expose_pool(value, current, index)
    return env_map


def load_and_bind(
    run: Any,
    *,
    tenant_id: str,
    environment_id: str,
    repo: Any,
    cursors: PoolCursorRegistry,
    logger: Any,
) -> None:
    """Resolve the selected environment and inject its ``$env`` source into the
    run context. Shared by StartRun and TestFullWorkflow. No-op (with a warning)
    when the environment is missing/inactive, cross-tenant, or unbound.
    """
    # SA-2496: log with plain messages (not structlog-style kwargs) so ANY logger
    # works — the stdlib logger used by the child-workflow coordinator as well as
    # the app's ILogger used by StartRun/TestFullWorkflow. structlog is not a
    # dependency of this service.
    if not environment_id or repo is None:
        return
    try:
        env = repo.get_by_id(environment_id)
    except Exception:
        logger.warning(
            f"environment lookup failed; running without $env "
            f"(environment_id={environment_id})"
        )
        return
    if env is None or not env.is_active or env.tenant_id != tenant_id:
        logger.warning(
            f"environment not found for run; running without $env "
            f"(environment_id={environment_id}, tenant_id={tenant_id})"
        )
        return
    run.context.variables["env"] = build_env_source(env, cursors)
    logger.info(
        f"environment bound to run "
        f"(run_id={getattr(run, 'id', '')}, environment_id={env.id}, "
        f"section={env.section}, name={env.name})"
    )

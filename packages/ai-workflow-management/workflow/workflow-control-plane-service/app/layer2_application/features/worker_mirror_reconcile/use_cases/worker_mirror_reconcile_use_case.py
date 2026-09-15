"""Reconcile the executor's in-memory worker registry into the HANA mirror
(``AIW_WORKER_REGISTRY_MIRROR``) — SA-2050 (R3).

One ``execute()`` = one snapshot-reconcile pass, driven by the CP background loop
(``main.py`` lifespan, guarded on ``WORKER_MIRROR_ENABLED``). Correctness rails
(spec §6/§7):

  * mock / no-HANA no-op — the repo is cross-wired only when ``infra.db`` exists
    (INFRA_MODE local/sap); when it is ``None`` the pass no-ops and never crashes;
  * empty-snapshot skip — ``list_workers()`` returns ``[]`` BOTH when the registry
    is genuinely empty AND when the executor is unreachable (indistinguishable),
    so an empty snapshot upserts nothing and deletes nothing — a transient blip
    can never wipe the whole mirror;
  * write errors swallowed — a HANA write failure is logged and swallowed so the
    loop survives; the mirror failing must never affect the palette or dispatch.

The ``reconcile`` call is synchronous (a bounded handful of small writes every
interval) inside the async ``execute``; acceptable for a low-frequency
observability loop.
"""

from __future__ import annotations

from typing import Any


class WorkerMirrorReconcileUseCase:
    def __init__(
        self,
        worker_registry_client: Any = None,
        logger: Any = None,
        **kwargs: Any,
    ) -> None:
        self._registry = worker_registry_client
        self.logger = logger
        # Cross-wired in ``build_app_container`` — the repo is not in
        # available_dependencies and isn't built at feature-discovery time.
        self._repo: Any = None

    async def execute(self) -> dict:
        # Mock / no-HANA → no-op (repo absent). Never touch the registry.
        if self._repo is None:
            return {"reconciled": False, "reason": "no_repo"}
        if self._registry is None:
            return {"reconciled": False, "reason": "no_registry"}
        try:
            rows = self._registry.list_workers() or []
        except Exception as exc:  # registry unreachable at the call site
            self._log(
                "error",
                "Worker mirror: registry unavailable — skipping tick",
                error=str(exc),
            )
            return {"reconciled": False, "reason": "registry_error"}
        # Empty-snapshot guard (§6): [] is ambiguous (empty OR unreachable) —
        # never reconcile, so a blip cannot wipe the mirror.
        if not rows:
            return {"reconciled": False, "reason": "empty_snapshot"}
        try:
            result = self._repo.reconcile(rows)
        except Exception as exc:  # swallow write errors — the loop must survive
            self._log(
                "error",
                "Worker mirror: reconcile failed — swallowed",
                error=str(exc),
            )
            return {"reconciled": False, "reason": "write_error"}
        return {
            "reconciled": True,
            "upserted": result.upserted,
            "deleted": result.deleted,
        }

    def _log(self, level: str, message: str, **fields: Any) -> None:
        if self.logger is not None:
            getattr(self.logger, level)(message, **fields)

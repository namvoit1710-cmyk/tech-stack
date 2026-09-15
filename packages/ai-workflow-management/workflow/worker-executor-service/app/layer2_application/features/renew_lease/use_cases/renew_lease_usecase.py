"""Renew lease use case (SA-2029, B4) — extend a long-running task's visibility TTL.

A pull worker whose task outlives the lease TTL renews the lease (CAS: task_id +
lease_token + state==LEASED) so the sweeper doesn't re-enqueue a task that is still
being worked. On a 0-row CAS the lease is gone/superseded → 409 TASK_SUPERSEDED, and
the worker must stop (its late callback would also be rejected — B5). The returned
value is the NEW absolute deadline; the worker renews against THAT, never its own clock.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.app_config_interface import IAppConfig
from app.layer2_application.interfaces.ready_task_store_interface import IReadyTaskStore


@dataclass
class RenewLeaseInput:
    task_id: str = ""
    lease_token: str = ""


class RenewLeaseUseCase:
    def __init__(
        self,
        logger: ILogger,
        ready_task_store: IReadyTaskStore | None = None,
        app_config: IAppConfig | None = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.ready_task_store = ready_task_store
        self._app_config = app_config

    def execute(self, input: RenewLeaseInput) -> dict:
        if self.ready_task_store is None or not input.task_id or not input.lease_token:
            return {"ok": False, "code": "TASK_SUPERSEDED"}

        # Resolve the per-type TTL from the row's worker_type (best-effort; a
        # concurrent sweep between get and renew only affects which TTL is used,
        # and renew's own CAS is authoritative for whether the extension applies).
        row = self.ready_task_store.get(input.task_id)
        ttl = self._lease_ttl_for(row.worker_type if row else "")

        new_deadline = self.ready_task_store.renew(input.task_id, input.lease_token, ttl)
        if new_deadline is None:
            self.logger.info(
                "Lease renew rejected (superseded)", task_id=input.task_id
            )
            return {"ok": False, "code": "TASK_SUPERSEDED"}
        return {"ok": True, "task_id": input.task_id, "deadline": new_deadline}

    def _lease_ttl_for(self, worker_type: str) -> int:
        default = int(getattr(self._app_config, "LEASE_TTL_SECONDS", 60) or 60)
        raw = getattr(self._app_config, "LEASE_TTL_OVERRIDES", "") or ""
        if not raw:
            return default
        try:
            overrides = json.loads(raw) if isinstance(raw, str) else dict(raw)
            return int(overrides.get(worker_type, default))
        except Exception:
            return default

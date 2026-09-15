"""Lease tasks use case (SA-2028, B3) — the pull worker's long-poll intake.

A pull worker calls this to atomically claim up to `max` READY tasks from its
`(tenant, worker_type)` partition (RT#1 CAS in the store), holding the connection
open up to `wait` seconds (long-poll) so a scale-to-zero worker isn't hammering the
endpoint. It is the pull dual of the executor POSTing `/execute-async`.

Scope note: auth (XSUAA → tenant) is B5 — B3 takes the tenant from a header, defaulting
to the single un-partitioned namespace. Admission / dedicated async listener / byte
accounting are B6. Renewal is B4. Payload is inline here; B6 swaps it for a payload_ref
pointer.
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.app_config_interface import IAppConfig
from app.layer2_application.interfaces.ready_task_store_interface import IReadyTaskStore, TaskRow
from app.layer2_application.services import pull_lifecycle


@dataclass
class LeaseTasksInput:
    worker_type: str = ""
    tenant: str = ""
    max_n: int = 1
    wait_seconds: float = 0.0
    # SA-2424: the leasing worker's instance id, so the store can hand a
    # per-instance-pinned task to exactly this instance (blank = legacy/any).
    instance_id: str = ""


class LeaseTasksUseCase:
    # Base poll cadence inside a long-poll; jittered to avoid a thundering herd of
    # scale-from-zero workers waking in lockstep.
    _POLL_INTERVAL = 0.5

    def __init__(
        self,
        logger: ILogger,
        ready_task_store: IReadyTaskStore | None = None,
        app_config: IAppConfig | None = None,
        monitor: Any = None,
        result_publisher: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.ready_task_store = ready_task_store
        self._app_config = app_config
        self._monitor = monitor
        self._result_publisher = result_publisher
        # In-process concurrent long-poll connection count, keyed PER (tenant,type)
        # partition (B6). The lease endpoint is async on one event loop, so the
        # get/++/-- between awaits is atomic. Per-partition (not one global counter)
        # so one hot tenant's flood can't shed every other tenant's leases.
        self._active_polls: dict[tuple[str, str], int] = {}

    async def execute(self, input: LeaseTasksInput) -> list[dict]:
        # Pull not wired (feature off) → no tasks, ever. Endpoint stays valid but
        # empty; never an error, so a probing worker just backs off.
        if self.ready_task_store is None or not input.worker_type:
            return []

        # Connection cap (B6): shed a long-poll if this partition already holds too
        # many open, so a flood of scale-from-zero pollers can't exhaust the
        # connection pool. Return [] immediately — the worker backs off and retries.
        key = (input.tenant, input.worker_type)
        max_conn = int(getattr(self._app_config, "MAX_LEASE_CONNECTIONS", 0) or 0)
        if max_conn and self._active_polls.get(key, 0) >= max_conn:
            if self._monitor and hasattr(self._monitor, "increment"):
                try:
                    self._monitor.increment("lease_connection_shed")
                except Exception:
                    pass
            return []

        self._active_polls[key] = self._active_polls.get(key, 0) + 1
        try:
            return await self._long_poll(input)
        finally:
            n = self._active_polls.get(key, 0) - 1
            if n <= 0:
                self._active_polls.pop(key, None)   # no empty-key leak
            else:
                self._active_polls[key] = n

    async def _long_poll(self, input: LeaseTasksInput) -> list[dict]:
        max_n = max(1, min(int(input.max_n or 1), self._max_batch()))
        wait = max(0.0, min(float(input.wait_seconds or 0.0), self._max_wait()))
        ttl = self._lease_ttl_for(input.worker_type)

        ceiling = int(getattr(self._app_config, "MAX_TOTAL_LEASED", 0) or 0) or None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            # In-memory claim is a sub-ms lock + scan, so a direct call is fine.
            # When the store graduates to a durable UPDATE…WHERE (SPEC §6), wrap
            # this in ``await asyncio.to_thread(...)`` so a blocking DB call can't
            # stall all concurrent long-polls on the event loop.
            leased = self.ready_task_store.claim(
                input.worker_type, input.tenant, max_n, ttl,
                max_total_leased=ceiling, instance_id=input.instance_id,
            )
            if leased:
                if self._monitor and hasattr(self._monitor, "increment"):
                    try:
                        self._monitor.increment("tasks_leased", len(leased))
                    except Exception:
                        pass
                # Lifecycle → WCP marks each node LEASED (B7), distinct from QUEUED.
                # publish_result is a SYNC broker send — run it off the event loop so
                # a slow/hung broker can't stall every concurrent long-poll leaser.
                if self._result_publisher:
                    for r in leased:
                        try:
                            await asyncio.to_thread(
                                self._result_publisher.publish_result, pull_lifecycle.leased_event(r)
                            )
                        except Exception as exc:
                            self.logger.warning("Failed to publish task.leased", task_id=r.task_id, error=str(exc))
                self.logger.info(
                    "Leased tasks to pull worker",
                    worker_type=input.worker_type,
                    tenant=input.tenant or "-",
                    count=len(leased),
                )
                return [self._to_lease_dto(r, ttl) for r in leased]

            remaining = deadline - loop.time()
            if remaining <= 0:
                return []  # long-poll expired with nothing available → 200 []
            # Jittered sleep, never overshooting the caller's wait budget.
            await asyncio.sleep(min(self._POLL_INTERVAL * (0.5 + random.random()), remaining))

    @staticmethod
    def _to_lease_dto(r: TaskRow, ttl_seconds: int) -> dict:
        """The lease response the SDK needs to run the task and report back.
        `lease_token`/`deadline` are the renewal + callback credentials (B4/B5);
        `payload` is inline for B3 (B6 replaces it with a payload_ref pointer).
        `lease_ttl_seconds` lets the worker DERIVE its renew interval from the
        executor's authoritative TTL (never a separately-configured value that
        could exceed the TTL) — B4."""
        return {
            "task_id": r.task_id,
            "run_id": r.run_id,
            "node_id": r.node_id,
            "worker_type": r.worker_type,
            "spec_version": r.spec_version,
            "payload": r.payload,
            "node_config": r.node_config,
            "idempotency_key": r.idempotency_key,
            "deadline": r.deadline,        # absolute (executor clock) — renew against THIS
            "lease_ttl_seconds": ttl_seconds,
            "lease_token": r.lease_token,
            "attempt_count": r.attempt_count,
        }

    def _max_batch(self) -> int:
        return int(getattr(self._app_config, "LEASE_MAX_BATCH", 16) or 16)

    def _max_wait(self) -> float:
        return float(getattr(self._app_config, "LEASE_MAX_WAIT_SECONDS", 20) or 20)

    def _lease_ttl_for(self, worker_type: str) -> int:
        """Per-type lease TTL (long-running types e.g. agent need a longer visibility
        timeout). LEASE_TTL_OVERRIDES is JSON {worker_type: seconds}; falls back to
        LEASE_TTL_SECONDS. Mirrors TASK_TIMEOUT_OVERRIDES (SA-1531)."""
        default = int(getattr(self._app_config, "LEASE_TTL_SECONDS", 60) or 60)
        raw = getattr(self._app_config, "LEASE_TTL_OVERRIDES", "") or ""
        if not raw:
            return default
        try:
            overrides = json.loads(raw) if isinstance(raw, str) else dict(raw)
            return int(overrides.get(worker_type, default))
        except Exception:
            self.logger.warning("LEASE_TTL_OVERRIDES unparseable — using default", default=default)
            return default

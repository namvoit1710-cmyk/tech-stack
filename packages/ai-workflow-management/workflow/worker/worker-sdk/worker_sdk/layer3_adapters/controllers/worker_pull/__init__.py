"""PULL run mode (SA-2028, B3) — the worker leases tasks instead of being pushed to.

A pull worker registers with delivery_mode=pull and NO reachable endpoint, then
long-polls the executor's lease endpoint. For each leased task it runs the SAME exec
core the SERVER/HEADLESS modes use (`execute_task` use case — off the event loop via
`to_thread`, so sync AND async handlers are covered per the SDK sync+async rule) and
reports the result to the SAME `/tasks/callback`, carrying the lease_token so the
executor can validate the lease (triple-binding is wired executor-side in B5).

The lease poll IS the primary liveness signal; the heartbeat is secondary. Renewal of
long-running leases lands in B4.
"""

import asyncio
import inspect
import logging
import random
import time
from typing import Any, Optional

import httpx

from worker_sdk.layer4_frameworks.config.app_config import settings
from worker_sdk.layer4_frameworks.config import instance_identity
from worker_sdk.layer1_domain.entities.worker_registration import WorkerRegistration
from worker_sdk.layer1_domain.value_objects.node_kind import NodeKind
from worker_sdk.layer1_domain.value_objects.worker_status import WorkerStatus
from worker_sdk.layer1_domain.value_objects.port import default_task_ports
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
    ExecuteTaskResult,
    assert_off_event_loop,
)
from worker_sdk.layer2_application.services.worker_concurrency import WorkerConcurrency

_log = logging.getLogger("WorkerSDK")


class PullTaskClient:
    """HTTP client for the executor's lease + callback endpoints (pull mode)."""

    def __init__(self, base_url: str, tenant: str = "", worker_type: str = "",
                 client: Optional[httpx.AsyncClient] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.tenant = tenant
        self.worker_type = worker_type
        # Lease requests hold open for the long-poll; give the client headroom
        # over LEASE_WAIT_SECONDS so the poll — not the client — decides timing.
        self._client = client or httpx.AsyncClient(
            timeout=float(getattr(settings, "LEASE_WAIT_SECONDS", 20.0)) + 15.0
        )

    def _headers(self) -> dict:
        return {"X-Tenant-Id": self.tenant} if self.tenant else {}

    @staticmethod
    def _unwrap(data: Any) -> Any:
        # Strip the executor's {status:ok, data:...} response envelope.
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data

    async def lease(self, worker_type: str, max_n: int, wait_seconds: float) -> list[dict]:
        url = f"{self.base_url}/api/v1/workers/type/{worker_type}/tasks/lease"
        # SA-2424: identify this instance so the executor can hand us a task pinned
        # to us (and NOT a task pinned to a sibling replica). Blank id off CF is fine
        # — the executor treats it as "unpinned only", unchanged legacy behavior.
        headers = dict(self._headers())
        iid = instance_identity.instance_id()
        if iid:
            headers["X-Instance-Id"] = iid
        resp = await self._client.post(
            url, params={"max": max_n, "wait": wait_seconds}, headers=headers
        )
        resp.raise_for_status()
        # Tolerate an out-of-contract / older-executor body: only a list is a
        # valid lease result. A dict/scalar/None → no tasks (never iterate it).
        data = self._unwrap(resp.json())
        return data if isinstance(data, list) else []

    async def post_callback(self, task_id: str, lease_token: str, result: Any) -> None:
        url = f"{self.base_url}/api/v1/tasks/callback"
        status = "success" if result.status == TaskStatus.SUCCESS else "error"
        body = {
            "task_id": task_id,
            "status": status,
            "outputs": result.outputs or {},
            "error": result.error,
            "duration_ms": result.duration_ms,
            "output_reference": getattr(result, "output_reference", "") or "",
            # worker_type is part of the triple-binding (checked vs the stored row).
            "worker_type": self.worker_type,
        }
        # Lease token is HEADER-only (X-Lease-Token), never in the body — the
        # executor's triple-binding CAS reads it from the header (SPEC §4/§5).
        headers = {**self._headers(), "X-Lease-Token": lease_token}
        resp = await self._client.post(url, json=body, headers=headers)
        resp.raise_for_status()

    async def renew(self, task_id: str, lease_token: str) -> Optional[str]:
        """Extend the lease. Returns the new absolute deadline, or None if the
        executor rejected it (409 TASK_SUPERSEDED — the lease is gone)."""
        url = f"{self.base_url}/api/v1/tasks/{task_id}/lease/renew"
        headers = {**self._headers(), "X-Lease-Token": lease_token}
        resp = await self._client.post(url, headers=headers)
        if resp.status_code == 409:
            return None
        resp.raise_for_status()
        data = self._unwrap(resp.json())
        return data.get("deadline") if isinstance(data, dict) else None

    async def close(self) -> None:
        await self._client.aclose()


# Fallback renewal budget for callers that do not pass one. It is a real cap,
# not a disabled sentinel: a safety mechanism that defaults to "off" is the bug
# it was written to prevent. ``settings.LEASE_RENEW_MAX_SECONDS`` is the runtime
# source of truth; ``run_pull_worker`` passes it. See that setting for why an
# uncapped renewer disarms the executor's lease-expiry recovery.
_DEFAULT_RENEW_MAX_SECONDS = 3600.0


def _renew_budget_spent(started_at: float, max_seconds: float) -> bool:
    """True once the renewal budget for a task is exhausted. Monotonic, so a
    clock step (NTP, VM resume) cannot end renewal early or extend it."""
    if max_seconds <= 0:
        return False   # explicit opt-out — renew for as long as the task runs
    return (time.monotonic() - started_at) >= max_seconds


async def _renew_loop(client: "PullTaskClient", task_id: str, lease_token: str,
                      stop: asyncio.Event, interval: float, logger: Any = None,
                      max_seconds: float = _DEFAULT_RENEW_MAX_SECONDS) -> None:
    """Renew the lease every *interval* seconds (monotonic — skew-free) until the
    task finishes (stop set), the executor EXPLICITLY supersedes the lease (409),
    or *max_seconds* of renewal have elapsed.

    Critical distinction (B4 red-team): `client.renew` returns None ONLY on a real
    409 supersede; a transient error (timeout / 5xx / reset) RAISES. A transient
    failure must NOT stop renewal — otherwise one network blip during a long task
    would silently let the lease expire and cause a duplicate execution. So: raise
    → retry next interval; None → stop.

    The *max_seconds* budget is the third exit and the reason this loop is safe to
    run at all. Renewal is driven by "the exec core has not returned", which is not
    "the task is still progressing": a wedged handler never returns, so without a
    budget this loop renews forever and the executor's ``sweep_expired_leases``
    never sees an expired deadline — no re-enqueue, no MAX_LEASE_ATTEMPTS, no
    POISON. The task stays LEASED and invisible until someone notices by hand.
    Spending the budget hands the task back to that net."""
    if interval <= 0:
        interval = 0.5   # never busy-spin if mis-called with <=0 (prod derives >= floor)
    started_at = time.monotonic()
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return  # task finished → stop renewing
        except asyncio.TimeoutError:
            pass
        if _renew_budget_spent(started_at, max_seconds):
            # Loud on purpose: the lease will now lapse and the executor will
            # re-enqueue or poison the task. That is the designed recovery, but
            # it is never routine — it means a handler ran past its budget.
            if logger:
                logger.error(
                    "Lease renewal budget exhausted; stopping renewal — the lease "
                    "will expire and the executor will reclaim this task",
                    task_id=task_id, max_seconds=max_seconds,
                )
            return
        try:
            new_deadline = await client.renew(task_id, lease_token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Transient — the lease is probably still valid. Keep trying.
            if logger:
                logger.warning("Lease renew errored (transient, will retry)",
                               task_id=task_id, error=str(exc))
            continue
        if new_deadline is None:
            # Explicit 409 supersede — the lease is gone. Stop; the task keeps
            # running (best-effort cancel) and its callback will be rejected.
            if logger:
                logger.warning("Lease renew superseded; stopping renewal", task_id=task_id)
            return


async def _batch_renew_loop(client: "PullTaskClient", leases: dict[str, str],
                            stop: asyncio.Event, interval: float,
                            superseded: set[str], logger: Any = None,
                            max_seconds: float = _DEFAULT_RENEW_MAX_SECONDS) -> None:
    """Renew EVERY lease in the batch, not just the task currently running.

    Why this exists (the per-task `_renew_loop` is not enough): the executor
    stamps ONE absolute deadline on the WHOLE batch at claim time —
    `memory_ready_task_store.claim` computes `deadline_iso` once, BEFORE its
    loop, then assigns that same value to every claimed row. Meanwhile this SDK
    drains the batch SEQUENTIALLY. So a renewal that starts only when a task
    begins leaves every still-queued task's lease ticking from claim time: with
    LEASE_MAX_BATCH > 1 and a task longer than the TTL, the queued ones expire,
    the executor re-enqueues them to another worker (duplicate execution), and
    this worker's eventual result is rejected because `sweep_expired_leases`
    cleared its `lease_token`.

    Each task carries its OWN `lease_token` (claim assigns a fresh one per row),
    so they are independently renewable — this loop simply does what the
    per-task one did, for all of them.

    Supersede handling mirrors `_renew_loop` exactly: `renew` returning None is
    an explicit 409, which retires that ONE task into *superseded* (the caller
    skips it); a transient error (raise) keeps it in rotation, because one
    network blip must never cause a duplicate execution.

    *max_seconds* bounds renewal the same way `_renew_loop` does, but the budget is
    BATCH-WIDE, not per task: the executor stamps one deadline on the whole batch at
    claim time, so every lease here started ticking at the same instant and one
    shared clock is the honest measure of "how long these leases have been held".
    Spending it therefore stops renewal for the whole batch in one go.

    Nothing is added to *superseded* when the budget runs out, and the distinction
    matters: superseded means "the executor already reclaimed this, do not run it"
    and makes the caller SKIP the task, whereas a spent budget leaves tasks still
    running on leases that have not lapsed yet. Marking them superseded would throw
    away results that may well still be accepted.
    """
    if interval <= 0:
        interval = 0.5   # never busy-spin (prod derives >= floor)
    started_at = time.monotonic()
    live = dict(leases)
    while live:
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return  # batch finished → stop renewing
        except asyncio.TimeoutError:
            pass
        if _renew_budget_spent(started_at, max_seconds):
            if logger:
                logger.error(
                    "Lease renewal budget exhausted for the batch; stopping renewal "
                    "— remaining leases will expire and the executor will reclaim them",
                    task_ids=sorted(live), max_seconds=max_seconds,
                )
            return
        for task_id, token in list(live.items()):
            try:
                new_deadline = await client.renew(task_id, token)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Transient — the lease is probably still valid. Keep trying.
                if logger:
                    logger.warning("Lease renew errored (transient, will retry)",
                                   task_id=task_id, error=str(exc))
                continue
            if new_deadline is None:
                # Explicit 409 supersede for THIS task only; the rest of the
                # batch keeps renewing.
                live.pop(task_id, None)
                superseded.add(task_id)
                if logger:
                    logger.warning("Lease renew superseded; dropping from batch renewal",
                                   task_id=task_id)


def _batch_renew_interval(tasks: list[dict], enabled: bool, fraction: float,
                          floor: float) -> float:
    """Renew interval for a whole batch = the SHORTEST per-task interval.

    A batch is claimed for one worker_type so the executor gives every row the
    same TTL; taking the min is defensive (a mixed/old executor body can't make
    us renew too slowly for some row). Tasks carrying no usable TTL contribute
    nothing; if none does, renewal stays off (0.0)."""
    intervals = [
        i for i in (_derive_renew_interval(t, enabled, fraction, floor) for t in tasks)
        if i > 0
    ]
    return min(intervals) if intervals else 0.0


async def _run_gated(run_one: Any, task: dict, gate: Any, logger: Any = None) -> None:
    """Run one task and ALWAYS give the slot back.

    The release lives in a finally because a handler that raises, or a shutdown that
    cancels mid-run, must not leak capacity — a leaked slot is permanent: the gate
    never recovers it and the worker's throughput drops for the life of the process.
    """
    try:
        await run_one(task)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # A single task failing to run/report must not kill the batch; its lease
        # will expire and the executor re-enqueues (B8).
        if logger:
            logger.error(f"Pull task {task.get('task_id')} failed: {exc}")
    finally:
        gate.release()


async def _drain_leased_batch(
    client: "PullTaskClient",
    tasks: list[dict],
    run_one: Any,
    *,
    renew_enabled: bool,
    renew_fraction: float,
    renew_floor: float,
    renew_max: float = _DEFAULT_RENEW_MAX_SECONDS,
    gate: Any = None,
    logger: Any = None,
) -> None:
    """Run one leased batch: renew every lease in it, skip the reclaimed ones.

    Shared by the single-type and multi-type loops on purpose. This block is the
    subtle part of pull — the executor stamps ONE absolute deadline on the WHOLE
    batch at claim time while the batch drains SEQUENTIALLY, so a later task's
    lease is already ticking while earlier ones run. Renewal has to span the
    batch, not the running task. Duplicating it is how a second implementation
    quietly gets it wrong, so there is exactly one copy.

    ``run_one`` is an async callable taking the task dict — the single-type path
    passes the exec core, the multi-type path passes its node handler.

    ``renew_max`` bounds the batch-wide renewal budget. It is threaded through
    rather than left to the default because a renewer with no ceiling disarms the
    executor's ``sweep_expired_leases`` net entirely: a wedged handler renews
    forever, the deadline never passes, and the task stays LEASED and invisible.

    ``gate`` decides whether the batch runs CONCURRENTLY. Without one the batch is
    drained one task at a time — the single-type path has no cap, so overlapping
    there would be unbounded, and PULL deliberately has no unlimited mode. With one,
    every task starts at once and the gate bounds how many actually run, which is the
    behaviour claude-worker/log-worker had before their ``pull_multi.py`` forks were
    deleted (their own test: "THE feature: three queued tasks must overlap, not run
    one after another"). The caller has already capped the lease request at free
    capacity, so in practice every task here can start immediately — which is exactly
    what makes the batch-wide renewal above sufficient rather than merely helpful.
    """
    interval = _batch_renew_interval(tasks, renew_enabled, renew_fraction, renew_floor)
    superseded: set[str] = set()
    stop_batch = asyncio.Event()
    batch_renew = None
    if interval > 0:
        leases = {
            str(t.get("task_id")): str(t.get("lease_token") or "")
            for t in tasks if t.get("task_id") and t.get("lease_token")
        }
        if leases:
            batch_renew = asyncio.create_task(
                _batch_renew_loop(client, leases, stop_batch, interval, superseded,
                                  logger, renew_max)
            )
    inflight: list[asyncio.Task] = []
    try:
        for task in tasks:
            if str(task.get("task_id")) in superseded:
                # The executor already reclaimed this lease (409) and has
                # re-enqueued it elsewhere. Running it would burn the full task
                # duration for a result whose callback is rejected on a dead
                # lease_token — skip and let the re-lease own it.
                if logger:
                    logger.warning("Skipping superseded task (lease reclaimed)",
                                   task_id=task.get("task_id"))
                continue
            if gate is None:
                try:
                    await run_one(task)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # A single task failing to run/report must not kill the loop; its
                    # lease will expire and the executor re-enqueues (B8).
                    if logger:
                        logger.error(f"Pull task {task.get('task_id')} failed: {exc}")
                continue
            if not gate.acquire():
                # Unreachable while the caller caps its lease at free capacity; if
                # the executor ever over-delivers, say so rather than silently
                # exceeding the cap. The un-run lease expires and is re-enqueued.
                if logger:
                    logger.warning("Concurrency gate saturated; leaving task to its "
                                   "lease expiry", task_id=task.get("task_id"))
                continue
            inflight.append(asyncio.create_task(_run_gated(run_one, task, gate, logger)))
        if inflight:
            await asyncio.gather(*inflight)
    finally:
        # Nothing may outlive the batch: cancel any task still running (a Ctrl-C
        # lands here mid-gather) and collect it before the renewer stops, so no
        # handler is still reporting through a client the caller is about to close.
        for t in inflight:
            t.cancel()
        for t in inflight:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        # Mirror process_leased_task's discipline: signal first so the loop
        # returns on its own, then await it — no orphan renewer outliving the batch.
        stop_batch.set()
        if batch_renew is not None:
            try:
                await batch_renew
            except asyncio.CancelledError:
                raise
            except Exception:
                pass


def _run_exec_core(execute_uc: Any, cmd: ExecuteTaskCommand) -> Any:
    """Thread-side entry point for the exec core.

    ``ExecuteTaskUseCase.execute`` asserts this itself, but the check is
    repeated at the dispatch boundary so a duck-typed / injected exec core
    (tests, embedders) is held to the same invariant. See
    ``assert_off_event_loop`` for why running here on the loop is fatal to
    lease renewal."""
    assert_off_event_loop()
    return execute_uc.execute(cmd)


async def process_leased_task(execute_uc: Any, client: PullTaskClient, task: dict,
                              logger: Any = None, renew_interval: float = 0.0,
                              renew_max_seconds: float = _DEFAULT_RENEW_MAX_SECONDS) -> None:
    """Run ONE leased task through the exec core and report via callback.

    Factored out (injectable execute_uc + client) so the loop body is unit-testable
    without a live executor. The exec core is sync; run it off the event loop so a
    slow handler can't stall the lease poller (mirrors HEADLESS). While it runs, a
    renewal task extends the lease every *renew_interval* seconds (B4) so a
    long-running task isn't reaped mid-flight; it is always cancelled/awaited in the
    finally so it can't outlive the task.

    ``renew_interval`` covers THIS task only, which is the whole story for a direct
    caller running a single task. ``run_pull_worker`` passes 0.0 and instead runs
    ``_batch_renew_loop`` across the entire lease batch — see that function for why
    per-task renewal is insufficient once LEASE_MAX_BATCH > 1."""
    task_id = task["task_id"]
    lease_token = task.get("lease_token", "")
    cmd = ExecuteTaskCommand(
        task_id=task_id,
        action="execute",
        inputs=task.get("payload", {}) or {},
        parameters=task.get("node_config", {}) or {},
        correlation_id=task.get("run_id"),
    )
    stop = asyncio.Event()
    renew_task = None
    if renew_interval and renew_interval > 0 and lease_token:
        renew_task = asyncio.create_task(
            _renew_loop(client, task_id, lease_token, stop, renew_interval, logger,
                        renew_max_seconds)
        )
    try:
        result = await asyncio.to_thread(_run_exec_core, execute_uc, cmd)
    finally:
        # stop.set() first so the renew loop returns on its own (it's awaiting the
        # event), then await it — no orphan on the normal path. Residual: on a
        # Ctrl-C landing here the completed result may go unreported → the lease
        # expires → B7 idempotency dedups the re-execution.
        stop.set()
        if renew_task is not None:
            try:
                await renew_task
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
    await client.post_callback(task_id, lease_token, result)
    if logger:
        logger.info("Pull task reported", task_id=task_id, status=str(result.status))


def _derive_renew_interval(task: dict, enabled: bool, fraction: float, floor: float) -> float:
    """Renew interval for a task = executor's lease_ttl_seconds * fraction, floored.
    Returns 0.0 (renewal off) when disabled or the lease carries no usable TTL. By
    deriving from the server's TTL the interval is structurally < TTL, so a lease
    can't expire before its first renew (B4 red-team)."""
    if not enabled:
        return 0.0
    ttl = float(task.get("lease_ttl_seconds") or 0.0)
    if ttl <= 0:
        return 0.0
    return max(floor, ttl * fraction)


async def _maybe_heartbeat(registry: Any, wid: Optional[str], reg: WorkerRegistration,
                           last_heartbeat: float, now: float, hb_interval: float,
                           logger: Any = None) -> tuple[Optional[str], float]:
    """Heartbeat iff ``hb_interval`` has elapsed. Returns the (possibly
    re-registered) worker id and the new ``last_heartbeat`` stamp.

    B-A (SA-2055): the caller invokes this on EVERY loop iteration, not just the
    idle one. The old code sat inside ``if not tasks:``, so a continuously busy
    pull worker never heartbeated at all and R4's sweeper would reap it mid-task.
    The ``>= hb_interval`` gate is carried over verbatim — only the branch
    placement moves. For a continuously busy worker the heartbeat RATE goes
    from 0 (never fired) to roughly ``1/hb_interval``; what's unchanged is the
    CAP the gate enforces — at most one heartbeat attempt per ``hb_interval``.

    Three behaviours the old block lacked:
      * ``re_register`` is honored, exactly as SERVER mode has always done
        (see the heartbeat loop in worker_server) — without it a pull worker
        never self-heals after an executor restart; it stays unregistered until
        redeploy.
      * a failure is LOGGED, not ``except Exception: pass``. Silence is how this
        survived. On failure the stamp is ALSO advanced to ``now`` (same as
        success), so a sustained outage attempts at most one heartbeat per
        ``hb_interval`` instead of retrying (and logging an error) on every
        busy-loop iteration. The lease poll is the PRIMARY liveness signal and
        has its own backoff, and ``re_register`` recovers a lost registration,
        so this throttle costs at most one extra ``hb_interval`` of recovery
        latency on a transient blip in exchange for avoiding a log/await storm
        during a sustained outage.
      * a BLANK ``wid`` re-registers (B-B). A failed boot register parks ``""``
        (run_pull_worker's boot block) and this is the only path that can clear
        it: pull workers used to boot against a dead executor and stay
        unregistered forever, since ``re_register`` needs a heartbeat response
        and a heartbeat needs a wid. A failed retry leaves the wid blank and
        does not advance the stamp; since a parked blank wid carries
        ``last_heartbeat == 0.0`` the gate is always open, so the retry runs on
        every loop tick (paced only by the idle floor) — forever, instead of
        never.
    """
    # B-B (SA-2055): `not wid` is GONE from this guard. It used to send a
    # blank/None wid straight back out, which is why a pull worker whose boot
    # register failed never recovered. Blank now falls through to the register
    # branch below. `registry is None` and the interval gate are unchanged —
    # the retry is throttled by the SAME clock as the heartbeat.
    if registry is None or (now - last_heartbeat) < hb_interval:
        return wid, last_heartbeat
    try:
        if not wid:
            # This worker has no registration: either the boot register threw
            # (run_pull_worker parks "") or a previous retry did. THIS is the
            # retry path. re_register alone cannot cover it: re_register only
            # ever arrives on a heartbeat RESPONSE, and you cannot heartbeat a
            # wid you never received.
            wid = await registry.register(reg)
            if logger:
                logger.info(f"Pull worker '{reg.worker_type}' registered as {wid} (recovered)")
        else:
            result = await registry.heartbeat(wid, WorkerStatus.HEALTHY)
            if isinstance(result, dict) and result.get("re_register"):
                if logger:
                    logger.info(f"Executor signaled re-registration for {reg.worker_type}, re-registering...")
                wid = await registry.register(reg)
                if logger:
                    logger.info(f"Re-registered {reg.worker_type} as {wid}")
        return wid, now
    except asyncio.CancelledError:
        # Explicit, matching this file's existing discipline (_renew_loop, the
        # lease block above). Ctrl-C must not be mistaken for a dead executor.
        raise
    except Exception as exc:
        # SA-2055 follow-up: an answer of "I have no such worker" is NOT a
        # transient failure. `re_register` only ever arrives on a 200 body, so an
        # executor (or the CF router in front of a restarting one) that answers
        # 404/410 instead used to leave a permanently stale wid — heartbeats went
        # on failing forever and the worker never re-registered.
        gone = bool(wid) and _registration_gone(exc)
        if logger:
            # Name what actually failed. `wid` is still blank here iff the
            # REGISTER attempt is what threw (a heartbeat failure — including a
            # nested re-register failure — leaves wid at its prior truthy
            # value, since a raising RHS never completes the assignment).
            what = "Heartbeat" if wid else "Re-registration"
            tail = f" — the executor does not know {wid}; re-registering" if gone else ""
            logger.error(f"{what} failed for {reg.worker_type}: {exc}{tail}")
        if gone:
            # Park a blank wid so the register branch above runs on the next
            # pass, and do NOT advance the stamp — the same rule as a failed
            # register: the retry must not be gated behind another full
            # interval. Bounded by construction: one successful register puts a
            # truthy wid back, so this cannot churn the registry per tick.
            return "", last_heartbeat
        # Deliberately asymmetric: a heartbeat failure still advances the
        # stamp to `now` (unchanged from before B-B — throttles a sustained
        # heartbeat outage to one attempt per interval). A register/retry
        # failure must NOT consume the interval: the executor was never
        # successfully talked to, so the retry has to be tried again next
        # tick, not gated behind another full hb_interval.
        return wid, (now if wid else last_heartbeat)


# --- registration upkeep, on a clock of its OWN ------------------------------
#
# HTTP statuses that mean "the executor does not know this registration", as
# opposed to "the executor could not answer right now". The first is a
# re-registration trigger; the second is a transient error to retry.
_REGISTRATION_GONE_STATUSES = frozenset({404, 410})

# The keeper never sleeps longer than the heartbeat interval, and never spins
# when that interval is 0 (tests and embedders disable the throttle that way).
_KEEPER_MIN_TICK = 0.01
# While any registration is still missing, retry FASTER than the heartbeat
# interval: a worker absent from the registry is handed no work at all, so a
# full HEARTBEAT_INTERVAL_SECONDS of silence is that much lost throughput.
_KEEPER_RETRY_TICK = 1.0


def _registration_gone(exc: BaseException) -> bool:
    """True when *exc* is the executor answering "I have no such worker".

    Duck-typed on purpose: httpx raises ``HTTPStatusError`` carrying a
    ``.response``, a gRPC/stub registry raises something else entirely, and only
    the former can carry this meaning. Anything unrecognised is treated as
    transient — the conservative side. A wrong "gone" verdict costs one extra
    register call (on CF the executor derives the registration id from
    instance_id x worker_type, so it lands on the same row); a wrong "transient"
    verdict is the permanent-zombie bug this helper exists to remove.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in _REGISTRATION_GONE_STATUSES


class _KeptRegistration:
    """One node type's registration state: its id, its payload, its own stamp."""

    __slots__ = ("wid", "reg", "last_heartbeat")

    def __init__(self, reg: WorkerRegistration) -> None:
        # Blank, NOT None — a blank wid is a REGISTRATION TODO, which
        # ``_maybe_heartbeat`` reads as "register me" (B-B, SA-2055).
        self.wid: str = ""
        self.reg = reg
        self.last_heartbeat: float = 0.0


class RegistrationKeeper:
    """Keeps a PULL worker's registrations alive on a clock of its OWN.

    THE bug this exists to kill (measured 2026-08-24, tenant-1): in PULL mode the
    heartbeat lived inside the lease loop, *after* the lease call, and a lease
    error did ``continue``. So for the whole time the executor was unreachable —
    exactly the window a restart opens — the worker never even attempted a
    heartbeat, and the heartbeat is the ONLY channel that carries ``re_register``.
    The process went on logging "running" while ``GET /api/v1/workers`` showed
    zero rows for its type and every lease 404'd, forever. Every DEPLOYED worker
    recovered from that same restart, because SERVER/HEADLESS mode heartbeats from
    an independent task (``worker_server._heartbeat_loop``) that no task-intake
    failure can gate. This class gives PULL the same shape.

    The second hole it closes: one lease-loop iteration drains a whole batch
    SEQUENTIALLY, so a per-iteration heartbeat is really "once per batch". Four
    five-minute tasks meant one heartbeat per twenty minutes, and R4's registry
    sweeper reaps on staleness — a live, busy worker could be swept mid-task.
    Cadence here is wall-clock, so what the tasks are doing cannot change it.

    Multi-type by construction: ``run_pull_worker`` passes one registration; the
    multi-type pull loops in claude-worker/log-worker pass one per node type. That
    is deliberate — those two carry a FORK of the pull LOOP but import the SDK's
    pieces, so keeping this a piece (not loop code) is what makes the fix reach
    them instead of needing a hand-port that will drift again.
    """

    def __init__(self, registry: Any, registrations: list, hb_interval: float,
                 logger: Any = None) -> None:
        self._registry = registry
        self._entries = [_KeptRegistration(r) for r in registrations]
        self._hb_interval = float(hb_interval)
        self._logger = logger

    @property
    def wids(self) -> list[str]:
        """Currently-held registration ids; blanks (never registered, or lost)
        are included so a caller can see what is still outstanding."""
        return [e.wid for e in self._entries]

    async def register_all(self) -> None:
        """Boot registration. Each type registers INDEPENDENTLY — one failure must
        not skip the rest — and a failure parks a blank wid rather than raising, so
        ``run`` picks it up as a retry within one tick instead of never."""
        if self._registry is None:
            return
        for e in self._entries:
            try:
                e.wid = await self._registry.register(e.reg)
                if self._logger:
                    self._logger.info(
                        f"Pull worker '{e.reg.worker_type}' registered as {e.wid}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                e.wid = ""      # -> the keeper loop retries it
                if self._logger:
                    self._logger.error(
                        f"Pull registration failed for '{e.reg.worker_type}' "
                        f"(will retry): {exc}")

    async def pass_once(self, now: float) -> None:
        """One upkeep pass over every registration. Never raises for a registry
        problem: ``_maybe_heartbeat`` owns that, and a dead executor must not take
        the keeper (or its caller) down with it."""
        if self._registry is None:
            return
        for e in self._entries:
            e.wid, e.last_heartbeat = await _maybe_heartbeat(
                self._registry, e.wid, e.reg, e.last_heartbeat, now,
                self._hb_interval, self._logger,
            )

    def _sleep_for(self) -> float:
        """How long until the next pass. Short while anything is unregistered.

        ``hb_interval <= 0`` means "no throttle" — the convention this suite's
        fakes use to make a 0.25 s test window cover many passes. It makes this a
        hot loop at ``_KEEPER_MIN_TICK`` and is NOT a production setting;
        ``HEARTBEAT_INTERVAL_SECONDS`` is an int defaulting to 30 and nothing
        documents 0 as "disabled", so reaching this branch on a real worker means
        someone configured it by hand.
        """
        if self._hb_interval <= 0:
            return _KEEPER_MIN_TICK
        if any(not e.wid for e in self._entries):
            return max(_KEEPER_MIN_TICK, min(_KEEPER_RETRY_TICK, self._hb_interval))
        return max(_KEEPER_MIN_TICK, self._hb_interval)

    async def run(self, stop: asyncio.Event) -> None:
        """Upkeep until *stop* is set. Pass FIRST, then sleep — so a keeper started
        after a failed boot register retries immediately rather than one interval
        late. ``asyncio`` timers never fire early, so sleeping exactly
        ``hb_interval`` cannot land a hair short of ``_maybe_heartbeat``'s ``>=``
        gate and silently halve the heartbeat rate."""
        if self._registry is None:
            return
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            await self.pass_once(loop.time())
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._sleep_for())
                return                   # stop set -> shut down
            except asyncio.TimeoutError:
                pass                     # next pass due

    async def deregister_all(self) -> None:
        """Give every held registration back. Blank wids are skipped — a type that
        never registered has nothing to undo, and deregistering "" would 404."""
        if self._registry is None:
            return
        for e in self._entries:
            if not e.wid:
                continue
            try:
                await self._registry.deregister(e.wid)
                if self._logger:
                    self._logger.info(
                        f"Pull worker {e.wid} ({e.reg.worker_type}) deregistered")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._logger:
                    self._logger.error(
                        f"Deregistration failed for {e.reg.worker_type}: {exc}")


async def _build_multi_pull_clients(types: list) -> dict:
    """One ``PullTaskClient`` per node type — each type is its own lease
    partition on the executor (``(tenant, worker_type)``), so each needs its own
    long-poll connection."""
    tenant = getattr(settings, "WORKER_TENANT", "")
    return {
        wt: PullTaskClient(settings.REGISTRY_URL, tenant=tenant, worker_type=wt)
        for wt, _nt in types
    }


def _build_pull_registration_for_node_type(nt: Any) -> WorkerRegistration:
    """One registration per node type, announced as pull.

    Mirrors ``worker_server._build_registration_for_node_type`` field for field —
    same schemas, kind, ports and functions reach the palette — differing only in
    ``endpoint``/``delivery_mode``, because a pull worker has no reachable URL.
    """
    func_defs = [f.to_definition() for f in (nt.functions or [])]
    return WorkerRegistration(
        worker_type=nt.worker_type,
        version=nt.version or settings.WORKER_VERSION,
        spec_version=getattr(nt, "spec_version", "1.0.0") or "1.0.0",
        endpoint="pull",
        delivery_mode="pull",
        sdk_version=settings.SDK_VERSION,
        input_schema=nt.input_schema,
        output_schema=nt.output_schema,
        name=nt.name or nt.worker_type,
        description=nt.description,
        node_class=nt.node_class,
        kind=getattr(nt, "kind", NodeKind.ACTION),
        icon=nt.icon,
        color=nt.color,
        tags=nt.tags,
        capabilities=nt.capabilities or [{"domain": nt.worker_type, "action": "execute"}],
        ports=nt.ports or default_task_ports(),
        functions=func_defs,
    )


async def _invoke_node_handler(nt: Any, inputs: Any, parameters: Any) -> Any:
    """Run a node type's handler, sync OR async — the SDK's standing rule.

    Identical dispatch to the HTTP path (``routes.py::_invoke_node_type_handler``):
    an ``async def`` is awaited on this loop, a plain ``def`` goes to a thread so a
    blocking handler cannot stall the lease pollers or lease renewal.

    Do NOT "simplify" this into a bare call inside ``asyncio.to_thread``. A coroutine
    function called that way returns a coroutine object rather than the outputs dict,
    which then ships as the node's output and fails far from here.
    """
    if inspect.iscoroutinefunction(nt.handler):
        return await nt.handler(inputs or {}, parameters or {})
    return await asyncio.to_thread(nt.handler, inputs or {}, parameters or {})


async def _process_leased_node_task(nt: Any, client: "PullTaskClient", task: dict,
                                    logger: Any = None) -> None:
    """Run ONE leased task through a node type's handler and report it.

    The multi-type counterpart of ``process_leased_task``. Renewal is not handled
    here for the same reason it is not there: ``_drain_leased_batch`` renews the
    whole batch around this call.

    Status vocabulary matters — a normal return is ``TaskStatus.SUCCESS`` and a raise
    is ``TaskStatus.ERROR``, exactly as the HTTP multi-type path does. The executor's
    callback pipeline treats ``"success"`` as the only success signal; anything else
    is reported to the control plane as WORKER_ERROR with the outputs still attached.
    """
    task_id = task["task_id"]
    lease_token = task.get("lease_token", "")
    t0 = time.monotonic()
    try:
        outputs = await _invoke_node_handler(
            nt, task.get("payload", {}) or {}, task.get("node_config", {}) or {},
        )
        result = ExecuteTaskResult(
            task_id=task_id, status=TaskStatus.SUCCESS, outputs=outputs or {},
            duration_ms=round((time.monotonic() - t0) * 1000, 2),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        result = ExecuteTaskResult(
            task_id=task_id, status=TaskStatus.ERROR, error=str(exc), outputs={},
            duration_ms=round((time.monotonic() - t0) * 1000, 2),
        )
    await client.post_callback(task_id, lease_token, result)
    if logger:
        logger.info("Pull task reported", task_id=task_id,
                    worker_type=nt.worker_type, status=str(result.status))


async def _lease_loop_for_type(worker_type: str, nt: Any, client: "PullTaskClient",
                               *, max_batch: int, wait_s: float, backoff: float,
                               idle_floor: float, renew_enabled: bool,
                               renew_fraction: float, renew_floor: float,
                               renew_max: float = _DEFAULT_RENEW_MAX_SECONDS,
                               gate: Any = None, logger: Any = None) -> None:
    """One type's lease→run→report loop. N of these run CONCURRENTLY.

    Concurrent rather than round-robin on purpose: the executor partitions the
    lease queue by ``(tenant, worker_type)``, so each type is a separate long-poll.
    Polling them in turn would leave every other type unpolled for the whole
    duration of a running task — fatal for a worker whose tasks run for minutes.
    Concurrency also means each poll can use the FULL ``LEASE_WAIT_SECONDS`` instead
    of dividing the budget by the number of types.

    Nothing here touches registration, and that is the point: the `continue` below
    used to be the bug in the single-type loop, because the heartbeat sat after it.
    ``RegistrationKeeper`` runs on its own clock, so a type whose lease endpoint is
    failing cannot take this worker out of the registry.

    *gate* is a ``WorkerConcurrency`` SHARED by every type loop, or None for no cap.
    Shared is the whole point: N loops each draining sequentially still put N tasks
    in flight, so a per-loop cap would silently multiply by the number of node types.
    It is consulted twice — before leasing, so this worker only claims what it can
    start (a lease sitting idle is a lease ticking against the executor's batch-wide
    deadline), and around the run itself, which is what actually enforces the cap.
    """
    loop = asyncio.get_running_loop()
    while True:
        t0 = loop.time()
        want = max_batch
        if gate is not None and gate.limited:
            free = gate.max_concurrent - gate.snapshot()["active"]
            if free <= 0:
                # Saturated: do not lease work this worker cannot start. Jittered so
                # N types do not resynchronise into a thundering herd on release.
                await asyncio.sleep(idle_floor * (0.5 + random.random()))
                continue
            want = min(max_batch, free)
        try:
            tasks = await client.lease(worker_type, want, wait_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if logger:
                logger.error(f"Lease failed for '{worker_type}': {exc}")
            await asyncio.sleep(backoff)
            continue

        if not tasks:
            # Same idle floor as single-type: the long-poll is best-effort and
            # returns [] IMMEDIATELY when pull is not enabled on that executor,
            # so without this the loop hot-spins. Jittered so N types (and a fleet
            # of workers) do not resynchronise into a thundering herd.
            elapsed = loop.time() - t0
            if elapsed < idle_floor:
                await asyncio.sleep((idle_floor - elapsed) * (0.5 + random.random()))
            continue

        await _drain_leased_batch(
            client, tasks,
            lambda t: _process_leased_node_task(nt, client, t, logger),
            renew_enabled=renew_enabled, renew_fraction=renew_fraction,
            renew_floor=renew_floor, renew_max=renew_max, gate=gate, logger=logger,
        )


async def _run_multi_type_pull(container: dict, node_type_registry: dict) -> None:
    """PULL for a worker that exposes several node types.

    Registers each type as its own pull node, then runs one lease loop per type
    concurrently, alongside the shared ``RegistrationKeeper``. Every property the
    single-type loop earned is kept: batch renewal with a bounded budget (shared
    ``_drain_leased_batch``), the jittered idle floor, registration upkeep on a
    clock no lease failure can gate, blank-wid retry, ``re_register``, 404-means-gone,
    and deregistration of everything that actually registered.
    """
    deps = container.get("_dependencies", {})
    registry = deps.get("worker_registry")
    logger = deps.get("logger")

    # Bound the default thread-pool executor (sync handlers run via to_thread).
    try:
        from concurrent.futures import ThreadPoolExecutor
        max_threads = max(1, int(getattr(settings, "MAX_WORKER_THREADS", 8)))
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(max_workers=max_threads, thread_name_prefix="worker-sdk-pull")
        )
    except Exception:
        _log.exception("Failed to size default executor in PULL; using Python default")

    types = list(node_type_registry.items())
    clients = await _build_multi_pull_clients(types)

    max_batch = int(getattr(settings, "LEASE_MAX_BATCH", 4))
    wait_s = float(getattr(settings, "LEASE_WAIT_SECONDS", 20.0))
    backoff = float(getattr(settings, "LEASE_ERROR_BACKOFF_SECONDS", 2.0))
    idle_floor = float(getattr(settings, "LEASE_IDLE_FLOOR_SECONDS", 1.0))
    hb_interval = float(getattr(settings, "HEARTBEAT_INTERVAL_SECONDS", 30))
    renew_enabled = bool(getattr(settings, "LEASE_RENEW_ENABLED", False))
    renew_fraction = float(getattr(settings, "LEASE_RENEW_SAFETY_FRACTION", 0.5))
    renew_floor = float(getattr(settings, "LEASE_RENEW_MIN_INTERVAL_SECONDS", 1.0))
    renew_max = float(getattr(settings, "LEASE_RENEW_MAX_SECONDS", _DEFAULT_RENEW_MAX_SECONDS))

    # ONE gate for the whole process, shared by every type loop. 0/unset = no cap,
    # the same meaning ``WORKER_MAX_CONCURRENT`` already has in SERVER mode
    # (SA-1530), so a multi-type pull worker is uncapped by default and its
    # concurrency is simply its node-type count.
    #
    # This came from claude-worker/log-worker's pull_multi.py forks, and carrying it
    # is what makes deleting them safe: without it claude-worker would go from a
    # configured 3 to 6 concurrent Claude CLI runs against one shared token pool
    # (its ``_TOKEN_IDX`` sticky index is deliberately unsynchronised, so parallel
    # runs burn the pool N times faster), and log-worker from 1 to 3. Both now pin
    # ``WORKER_MAX_CONCURRENT`` explicitly rather than inheriting a fork's default.
    max_conc = int(getattr(settings, "WORKER_MAX_CONCURRENT", 0) or 0)
    gate = WorkerConcurrency(max_conc) if max_conc > 0 else None

    keeper = RegistrationKeeper(
        registry,
        [_build_pull_registration_for_node_type(nt) for _wt, nt in types],
        hb_interval,
        logger,
    )
    await keeper.register_all()

    keeper_stop = asyncio.Event()
    running: list[asyncio.Task] = []
    try:
        if logger:
            logger.info(f"Pull worker running (multi-type, leasing {len(types)} node "
                        f"types: {', '.join(wt for wt, _ in types)}). Ctrl+C to stop.")
        running.append(asyncio.create_task(keeper.run(keeper_stop)))
        for wt, nt in types:
            running.append(asyncio.create_task(_lease_loop_for_type(
                wt, nt, clients[wt], max_batch=max_batch, wait_s=wait_s,
                backoff=backoff, idle_floor=idle_floor, renew_enabled=renew_enabled,
                renew_fraction=renew_fraction, renew_floor=renew_floor,
                renew_max=renew_max, gate=gate, logger=logger,
            )))
        await asyncio.gather(*running)
    except asyncio.CancelledError:
        pass
    finally:
        # Stop the keeper BEFORE deregistering, for the same reason the single-type
        # path does: a pass landing after the deregister would re-register on the
        # way out and leave a ghost row.
        keeper_stop.set()
        for t in running:
            t.cancel()
        for t in running:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await keeper.deregister_all()
        for c in clients.values():
            try:
                await c.close()
            except Exception:
                pass
        if registry is not None and hasattr(registry, "close"):
            await registry.close()


def _build_pull_registration() -> WorkerRegistration:
    return WorkerRegistration(
        worker_type=settings.WORKER_TYPE,
        version=settings.WORKER_VERSION,
        spec_version=getattr(settings, "WORKER_SPEC_VERSION", "1.0.0") or "1.0.0",
        endpoint="pull",              # no reachable server; the lease poll is the intake
        delivery_mode="pull",
        sdk_version=settings.SDK_VERSION,
        name=settings.WORKER_NAME or settings.WORKER_TYPE,
        description=settings.WORKER_DESCRIPTION,
        node_class=settings.WORKER_NODE_CLASS,
        icon=settings.WORKER_ICON,
        color=settings.WORKER_COLOR,
        tags=settings.get_tags_list(),
        capabilities=[{"domain": settings.WORKER_TYPE, "action": "execute"}],
        ports=default_task_ports(),
    )


async def run_pull_worker(container: dict) -> None:
    """Standalone async process for PULL mode: register pull, then lease→run→report."""
    deps = container.get("_dependencies", {})
    registry = deps.get("worker_registry")
    # Accept BOTH keys. ``build_app_container`` names every discovered feature
    # ``<module>_usecase`` (bootstrap.py: ``registry[f"{module_name}_usecase"]``),
    # so a real container carries ``execute_task_usecase`` and NEVER
    # ``execute_task`` — reading only the short name meant PULL raised
    # "requires an execute_task use case" on startup for every worker built the
    # documented way. It went unnoticed because every pull test hand-builds
    # ``{"execute_task": ...}``, a shape the bootstrap does not produce.
    # The short name is kept first for those callers (tests, embedders).
    execute_uc = container.get("execute_task") or container.get("execute_task_usecase")
    logger = deps.get("logger")

    # Multi-type worker (``run_worker(node_types=[...])``): each type is its own
    # lease partition on the executor — ``(tenant, worker_type)`` — so it needs its
    # own registration and its own poller. Handled by a dedicated runner; the
    # single-type body below is left exactly as it was. Dispatch happens BEFORE the
    # ``execute_uc is None`` check on purpose: a multi-type worker routes through
    # node handlers and has no shared exec core to require.
    node_type_registry = deps.get("node_type_registry")
    if node_type_registry:
        return await _run_multi_type_pull(container, node_type_registry)

    # Bound the default thread-pool executor (sync handlers run via to_thread).
    try:
        from concurrent.futures import ThreadPoolExecutor
        max_threads = max(1, int(getattr(settings, "MAX_WORKER_THREADS", 8)))
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(max_workers=max_threads, thread_name_prefix="worker-sdk-pull")
        )
    except Exception:
        _log.exception("Failed to size default executor in PULL; using Python default")

    if execute_uc is None:
        raise RuntimeError("PULL mode requires an execute_task use case in the container")

    # Registration upkeep runs on its OWN clock, in its OWN task — deliberately
    # not inside the lease loop. See ``RegistrationKeeper`` for the production
    # failure that shape exists to prevent: a lease error used to ``continue``
    # past the heartbeat, so the one channel that carries ``re_register`` went
    # silent for exactly as long as the executor was unreachable, and the worker
    # never came back from a restart. A blank wid inside the keeper is still a
    # REGISTRATION TODO (B-B, SA-2055) — the keeper is now what retries it.
    keeper = RegistrationKeeper(
        registry,
        [_build_pull_registration()],
        float(getattr(settings, "HEARTBEAT_INTERVAL_SECONDS", 30)),
        logger,
    )
    await keeper.register_all()

    client = PullTaskClient(
        settings.REGISTRY_URL,
        tenant=getattr(settings, "WORKER_TENANT", ""),
        worker_type=settings.WORKER_TYPE,
    )
    max_batch = int(getattr(settings, "LEASE_MAX_BATCH", 4))
    wait_s = float(getattr(settings, "LEASE_WAIT_SECONDS", 20.0))
    backoff = float(getattr(settings, "LEASE_ERROR_BACKOFF_SECONDS", 2.0))
    idle_floor = float(getattr(settings, "LEASE_IDLE_FLOOR_SECONDS", 1.0))
    renew_enabled = bool(getattr(settings, "LEASE_RENEW_ENABLED", False))
    renew_fraction = float(getattr(settings, "LEASE_RENEW_SAFETY_FRACTION", 0.5))
    renew_floor = float(getattr(settings, "LEASE_RENEW_MIN_INTERVAL_SECONDS", 1.0))
    renew_max = float(getattr(settings, "LEASE_RENEW_MAX_SECONDS", _DEFAULT_RENEW_MAX_SECONDS))
    loop = asyncio.get_running_loop()
    keeper_stop = asyncio.Event()
    keeper_task = asyncio.create_task(keeper.run(keeper_stop))

    try:
        if logger:
            logger.info("Pull worker running (leasing). Press Ctrl+C to stop.")
        while True:
            t0 = loop.time()
            try:
                tasks = await client.lease(settings.WORKER_TYPE, max_batch, wait_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if logger:
                    logger.error(f"Lease failed: {exc}")
                await asyncio.sleep(backoff)
                continue

            if not tasks:
                # Long-poll returned empty (idle). CRITICAL: the server long-poll is
                # only best-effort — it returns [] IMMEDIATELY when pull isn't enabled
                # on that executor (the default!) or when wait=0. Without a floor the
                # loop would hot-spin a core and flood lease+heartbeat requests. Sleep
                # so empty polls never exceed ~1/idle_floor; when the server DID honor
                # the full long-poll (elapsed >= idle_floor) we re-poll immediately.
                elapsed = loop.time() - t0
                if elapsed < idle_floor:
                    await asyncio.sleep((idle_floor - elapsed) * (0.5 + random.random()))
                continue

            # Tasks in a batch are drained SEQUENTIALLY, and the executor stamps one
            # absolute deadline on the whole batch at claim time — so a later task's
            # lease is already ticking while earlier ones run. Renewal therefore has
            # to span the batch, not the current task. `_drain_leased_batch` is that
            # one copy, shared with the multi-type loop.
            await _drain_leased_batch(
                client, tasks,
                lambda t: process_leased_task(execute_uc, client, t, logger, 0.0),
                renew_enabled=renew_enabled, renew_fraction=renew_fraction,
                renew_floor=renew_floor, renew_max=renew_max, logger=logger,
            )
    except asyncio.CancelledError:
        pass
    finally:
        # Stop the keeper BEFORE deregistering. A keeper pass that landed after
        # the deregister would re-register this worker on its way out and leave
        # a ghost row in the registry that only the sweeper could clear.
        keeper_stop.set()
        try:
            await keeper_task
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await keeper.deregister_all()
        await client.close()
        if registry is not None and hasattr(registry, "close"):
            await registry.close()

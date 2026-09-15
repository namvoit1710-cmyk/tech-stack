"""Worker registry store protocol."""
from typing import Protocol

from app.layer1_domain.entities.worker_registration import WorkerRegistration


class IWorkerRegistryStore(Protocol):
    """Protocol for worker registration persistence."""

    # SA-2049 — the store keys by the REGISTRATION id (`worker.id`, derived by R1 as
    # `<instance_id>:<worker_type>`): one row per (process x worker_type) PAIR. NEVER
    # by `worker_type` alone (a CLASS -- N replicas collapse into one row) and NEVER
    # by `instance_id` alone (one process serves N types -- all but the last are
    # destroyed). See SA-2047 §2.
    def register(self, worker: WorkerRegistration) -> None: ...
    def heartbeat(self, worker_id: str, status: str = "healthy") -> bool: ...
    def discover(self, capability: str) -> list[WorkerRegistration]: ...
    def deregister(self, worker_id: str) -> None: ...
    def get(self, worker_id: str) -> WorkerRegistration | None: ...
    def get_by_worker_type(self, worker_type: str) -> WorkerRegistration | None: ...
    def list_all(self) -> list[WorkerRegistration]: ...
    # SA-2049 — aggregate-by-type. Read by R6 (/workers/replicas), R7 (gauges),
    # R9 (realtime feed) and R10 (the /workers page). HanaWorkerRegistryStore owes
    # these too, but it is currently orphaned (imported only by its own test;
    # bootstrap.py wires the memory store) — wiring + parity is R3 (SA-2050).
    #
    # CONTRACT for every implementer: a "replica" is a PROCESS.
    #   `count_healthy(T)`   -> COUNT(DISTINCT instance_id) WHERE worker_type = T
    #                           AND the row reports healthy. Never a row count.
    #   `replica_summary()`  -> per worker_type: total = DISTINCT instance_id,
    #                           healthy = DISTINCT instance_id with a healthy row,
    #                           unhealthy = total - healthy.
    #   `list_instances(T)`  -> the ROWS for one type. Within a type, rows and
    #                           processes coincide, so len() is that type's replica
    #                           count -- but that does NOT hold store-wide.
    # In the memory store the DISTINCT is arithmetically redundant today; in a store
    # that can hold two rows for one (process, type) it is not. Implement the DISTINCT.
    def list_instances(self, worker_type: str) -> list[WorkerRegistration]: ...
    def count_healthy(self, worker_type: str) -> int: ...
    def replica_summary(self) -> dict[str, dict[str, int]]: ...
    def app_summary(self) -> dict[str, dict]: ...
    def list_app_instances(self, worker_app: str) -> list[tuple[WorkerRegistration, list[str]]]: ...

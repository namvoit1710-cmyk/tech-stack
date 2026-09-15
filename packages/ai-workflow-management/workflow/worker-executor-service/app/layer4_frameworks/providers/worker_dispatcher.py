"""Worker dispatcher — routes tasks to the correct worker executor by worker_id."""

from typing import TYPE_CHECKING

from app.layer1_domain.errors import InstancePinUnavailableError
from app.layer1_domain.value_objects.worker_liveness import HEALTHY_STATUSES
from app.layer4_frameworks.providers.http_worker_executor import HttpWorkerExecutor

if TYPE_CHECKING:
    from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore


class WorkerDispatcher:
    """Dispatches tasks to worker executors.

    Workers can be:
    1. Statically registered via register_worker() - for known, fixed workers
    2. Dynamically looked up from registry_store - for workers registered at runtime
    """

    def __init__(self, registry_store: "IWorkerRegistryStore | None" = None) -> None:
        self._executors: dict[str, HttpWorkerExecutor] = {}
        self._registry_store = registry_store

    def register_worker(self, worker_id: str, executor: HttpWorkerExecutor) -> None:
        """Register a static worker executor."""
        self._executors[worker_id] = executor

    def get_executor(self, worker_id: str) -> HttpWorkerExecutor | None:
        """Get an executor for the given worker_id.

        Looks up by:
        1. Static executor registry (pre-registered workers)
        2. Registry store by UUID
        3. Registry store by worker_type (stable identifier)
        """
        executor = self._executors.get(worker_id)
        if executor:
            return executor

        if self._registry_store:
            # Try UUID lookup first
            worker = self._registry_store.get(worker_id)
            if worker and worker.endpoint_url:
                return HttpWorkerExecutor(worker.endpoint_url)

            # Fallback: treat worker_id as worker_type (stable identifier)
            worker = self._registry_store.get_by_worker_type(worker_id)
            if worker and worker.endpoint_url:
                return HttpWorkerExecutor(worker.endpoint_url)

        return None

    def get_executor_for_instance(
        self, target_instance_id: str, worker_type: str = "", target_host: str = ""
    ) -> HttpWorkerExecutor:
        """Resolve the executor for a SPECIFIC worker instance (SA-2424 debug pin).

        ``target_instance_id`` may be the full registration id
        (``<instance_id>:<worker_type>``) or the bare ``instance_id``. SA-2437:
        ``target_host`` is a routing FALLBACK — when the pinned instance_id is
        stale/absent (synthesized ids churn per restart) but a live registration
        of this worker_type still runs on that host, dispatch there.

        Unlike ``get_executor`` this NEVER falls back to an arbitrary instance:
        the whole point of a debug pin is "run on this instance/host and nowhere
        else". A pin that cannot be honored raises ``InstancePinUnavailableError``
        so the caller fails the task fast.
        """
        tid = (target_instance_id or "").strip()
        host = (target_host or "").strip()
        if not tid and not host:
            raise InstancePinUnavailableError(tid, worker_type, "no target_instance_id or target_host given")
        if not self._registry_store:
            raise InstancePinUnavailableError(tid or host, worker_type, "no worker registry available")

        # 1) by instance_id: full registration id, then <instance_id>:<worker_type>,
        #    then scan the type's rows (covers ids not following the pair scheme).
        worker = None
        if tid:
            worker = self._registry_store.get(tid)
            if worker is None and worker_type:
                worker = self._registry_store.get(f"{tid}:{worker_type}")
            if worker is None and worker_type:
                for w in self._registry_store.list_instances(worker_type):
                    if tid in (w.instance_id, w.id):
                        worker = w
                        break
        # 2) SA-2437 host fallback: a live registration of this worker_type on
        #    that host (prefer a healthy one). Stable across instance_id churn.
        if worker is None and host and worker_type:
            candidates = [w for w in self._registry_store.list_instances(worker_type) if w.host == host]
            healthy = [w for w in candidates if w.health_status in HEALTHY_STATUSES]
            worker = (healthy or candidates)[0] if (healthy or candidates) else None
        if worker is None:
            raise InstancePinUnavailableError(tid or host, worker_type, "no registered instance matches the pin (instance_id/host)")
        if worker_type and worker.worker_type != worker_type:
            raise InstancePinUnavailableError(
                tid, worker_type,
                f"instance is worker_type {worker.worker_type!r}, not {worker_type!r}",
            )
        if worker.health_status not in HEALTHY_STATUSES:
            raise InstancePinUnavailableError(
                tid, worker_type, f"instance is not healthy (status={worker.health_status!r})"
            )
        if not worker.endpoint_url:
            raise InstancePinUnavailableError(tid, worker_type, "instance has no endpoint_url")
        return HttpWorkerExecutor(worker.endpoint_url)

    def get_executor_by_capability(self, capability: str) -> HttpWorkerExecutor | None:
        """Get an executor for a worker matching the given capability."""
        if not self._registry_store:
            return None

        from app.layer2_application.services.discovery_engine import DiscoveryEngine
        from app.layer2_application.services.load_balancer import LoadBalancer

        workers = DiscoveryEngine.find_workers(capability, self._registry_store)
        selected = LoadBalancer.select_worker(workers)

        if selected and selected.endpoint_url:
            return HttpWorkerExecutor(selected.endpoint_url)

        return None

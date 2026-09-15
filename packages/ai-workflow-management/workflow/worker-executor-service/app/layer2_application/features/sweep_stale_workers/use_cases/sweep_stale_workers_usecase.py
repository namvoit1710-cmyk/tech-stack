"""Sweep stale workers (SA-2051, R4) -- the liveness sweeper's orchestration half.

Runs MemoryWorkerRegistryStore.sweep_stale on the shared periodic loop (main.py),
then emits catalog.changed(deregistered, worker_type) on the broker rail ONCE for
each worker_type this sweep drove to 0 healthy. The store owns the timestamp math;
this use case owns the events -- the same split as sweep_pull_tasks.

Emits via the broker (publish_to_topic), NOT result_publisher: the control-plane's
CatalogChangedConsumer listens on the broker topic.
"""

from app.layer1_domain.value_objects.worker_liveness import replica_gauge_stats

CATALOG_CHANGED_TOPIC = "catalog.changed"


class SweepStaleWorkersUseCase:
    def __init__(self, logger, registry_store=None, message_broker_client=None,
                 app_config=None, monitor=None, **kwargs):
        self.logger = logger
        self.registry_store = registry_store
        self.message_broker_client = message_broker_client
        self._app_config = app_config
        self._monitor = monitor

    def execute(self) -> dict:
        if self.registry_store is None:
            return {"marked": 0, "removed": 0, "emptied": 0}
        stale_after = int(getattr(self._app_config, "WORKER_STALE_AFTER_SECONDS", 120) or 120)
        remove_after = int(getattr(self._app_config, "WORKER_REMOVE_AFTER_SECONDS", 600) or 600)
        result = self.registry_store.sweep_stale(stale_after, remove_after)
        if self._monitor and hasattr(self._monitor, "refresh_replica_gauges"):
            try:
                snapshot = replica_gauge_stats(self.registry_store.list_all(), stale_after)
                self._monitor.refresh_replica_gauges(snapshot)
            except Exception as exc:
                self.logger.warning("replica gauge refresh failed: %s", exc)
        for worker_type, spec_version in result.emptied:
            self._emit_deregistered(worker_type, spec_version)
        return {"marked": result.marked_count, "removed": result.removed_count,
                "emptied": len(result.emptied)}

    def _emit_deregistered(self, worker_type, spec_version):
        if self.message_broker_client is None or not worker_type:
            return
        try:
            self.message_broker_client.publish_to_topic(
                CATALOG_CHANGED_TOPIC,
                {"action": "deregistered", "worker_type": worker_type,
                 "spec_version": spec_version, "tenant_id": "system"},
            )
        except Exception as exc:
            self.logger.warning("catalog.changed publish failed (liveness sweep): %s", exc)

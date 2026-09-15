from typing import Any, Optional, Protocol


class IWorkerRegistryClient(Protocol):
    """Port for querying the worker registry."""

    def list_workers(self) -> list[dict[str, Any]]:
        """Return all registered workers. Returns [] if unreachable."""
        ...

    def get_worker(self, worker_id: str) -> Optional[dict[str, Any]]:
        """Return a single worker by ID. Returns None if not found."""
        ...

    def get_worker_by_type(self, worker_type: str) -> Optional[dict[str, Any]]:
        """Return the first worker matching ``worker_type``. Returns None if not found."""
        ...

    def get_replica_summary(self) -> Optional[list[dict[str, Any]]]:
        """Return the R6 per-type replica aggregate (GET /workers/replicas).

        None  -> the executor was UNREACHABLE (transport error/timeout/5xx).
        []    -> a real 200 with an empty registry (every worker deregistered).
        The two are DIFFERENT facts; the R9 differ treats them differently."""
        ...

    def get_type_replicas(self, worker_type: str) -> Optional[list[dict[str, Any]]]:
        """Return the R6 per-instance detail rows for one ``worker_type``
        (GET /workers/type/{worker_type}/replicas).

        None  -> the executor was UNREACHABLE (transport error/timeout/5xx).
        []    -> a real 200 with no instances for this worker_type.
        Same tri-state contract as :meth:`get_replica_summary` (SA-2047 R10)."""
        ...

    def get_app_summary(self) -> Optional[list[dict[str, Any]]]:
        """Return the R14 per-app aggregate (GET /workers/apps): one row per
        distinct worker APP (as opposed to per worker_type/replica).

        None  -> the executor was UNREACHABLE (transport error/timeout/5xx),
                 OR the executor answered 200 with a body that was NOT a
                 list (a contract violation on this route).
        []    -> a real 200 with no worker apps registered.

        DELIBERATE DIVERGENCE from :meth:`get_replica_summary` /
        :meth:`get_type_replicas`: those two fail soft to ``[]`` (with a
        loud warning) on a non-list body. This method does NOT — it folds
        a non-list body into ``None`` instead. Reason: a contract violation
        is not the same fact as "there are no workers", and Task 15 rekeys
        the R9 realtime feed onto this method, where ``None`` means
        "unreachable — keep the last snapshot, publish nothing" but ``[]``
        means "genuinely empty — tombstone every entry". Reporting a
        malformed body as ``[]`` would therefore wipe the entire fleet from
        the live view because the executor sent one bad response. Staying
        silent (``None``) is the safer failure. Whether ``[]``-on-non-list
        is right for the two older replica methods is a separate, explicitly
        out-of-scope question — they are untouched by this decision."""
        ...

    def get_app_instances(self, worker_app: str) -> Optional[list[dict[str, Any]]]:
        """Return every PROCESS instance of one worker app
        (GET /workers/apps/{worker_app}/instances).

        None  -> the executor was UNREACHABLE (transport error/timeout/5xx),
                 OR the body was NOT a list (contract violation).
        []    -> a real 200 with no instances for this app (including an
                 unknown app name — in a soft-state registry "no instances"
                 and "does not exist" are the same fact).

        DELIBERATE DIVERGENCE from :meth:`get_replica_summary` /
        :meth:`get_type_replicas` (same reasoning as :meth:`get_app_summary`
        above): those two fail soft to ``[]`` on a non-list body; this
        method folds a non-list body into ``None`` instead, because R9
        (Task 15) treats ``None`` as "unreachable, hold last snapshot" and
        ``[]`` as "genuinely empty, tombstone" — a malformed body reported
        as ``[]`` would wrongly tombstone this app's instances."""
        ...

    def get_worker_schema(self, worker_id: str) -> Optional[dict[str, Any]]:
        """Return input/output schema for a worker. Returns None if not found."""
        ...

    def discover_workers(self, capability: str) -> list[dict[str, Any]]:
        """Discover workers matching a capability. Returns [] on failure."""
        ...

    def list_worker_functions(self, worker_type: str) -> list[dict[str, Any]]:
        """Return functions declared by a worker. Returns [] if not found."""
        ...

    def invoke_worker_function(self, worker_type: str, function_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Invoke a named function on a worker. Returns {status, result, error}."""
        ...

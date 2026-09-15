from typing import Protocol, Any


class IResultReporter(Protocol):
    def report_completed(
        self,
        run_id: str,
        task_id: str,
        output_reference: str = "",
        output_metadata: dict[str, Any] | None = None,
        # SA-2032: the stable fence — carried on the AUTHORITATIVE sync (HTTP/gRPC)
        # completion too, not just the event-mesh publish, so HTTP-mode WCP can
        # reject a stale-generation result and dedup a re-driven task.
        run_generation: str = "",
        idempotency_key: str = "",
    ) -> dict: ...

    def report_failed(
        self,
        run_id: str,
        task_id: str,
        error: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        run_generation: str = "",
        idempotency_key: str = "",
    ) -> dict: ...

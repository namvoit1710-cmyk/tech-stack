import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.layer4_frameworks.config.app_config import settings
from app.layer3_adapters.controllers.restful.v1 import (
    task_router,
    worker_router,
    heartbeat_router,
    observability_router,
)
from app.layer3_adapters.controllers.restful.v1.middleware.error_handler import (
    app_error_handler as wes_app_error_handler,
    generic_error_handler as wes_generic_error_handler,
)
from app.layer3_adapters.controllers.restful.v1.middleware.response_wrapper import ResponseWrapperMiddleware
from app.layer4_frameworks.config.exceptions import AppError as WesAppError
from bootstrap import build_app_container


_grpc_server = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start background sweeper and optional gRPC server."""
    global _grpc_server
    container = app.state.container
    sweep_uc = container.get("sweep_timed_out_tasks")
    pull_sweep_uc = container.get("sweep_pull_tasks")  # B8 — pull-lease sweeper fork
    stale_sweep_uc = container.get("sweep_stale_workers")  # R4 (SA-2051) — liveness sweeper

    # Pass the main event loop to broker client so background consumer
    # threads can schedule async handlers without creating throwaway loops.
    _main_loop = asyncio.get_running_loop()
    broker = container.get("_broker")
    if broker is not None and hasattr(broker, "set_event_loop"):
        broker.set_event_loop(_main_loop)

    # Event Mesh task intake (WCP → executor dispatch over Event Mesh).
    # The broker poll thread invokes _on_task for each message on
    # 'workflow.tasks'; ExecuteTaskUseCase runs the worker over HTTP and
    # publishes the result to 'workflow.results'.
    if settings.TRANSPORT_MODE.lower() == "event_mesh" and broker is not None:
        execute_uc = container.get("execute_task")
        if execute_uc is not None:
            import logging

            from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
                ExecuteTaskInput,
            )

            _log = logging.getLogger(__name__)

            def _on_task(data: dict):
                # Run the task. Exceptions PROPAGATE to the broker poll loop,
                # which then skips the ack so Event Mesh redelivers (at-least-once
                # — paired with the executor's store_if_absent dedup so a redeliver
                # never double-dispatches). The CONCURRENCY_LIMIT result is the
                # backpressure signal: the executor was at capacity and did NOT
                # dispatch, so we raise to skip the ack and let the broker
                # redeliver after the lock TTL once a slot frees — instead of
                # acking and silently dropping the task.
                result = execute_uc.execute(ExecuteTaskInput(**data))
                # ADMISSION_FULL (B6): pull queue byte-quota full — same backpressure
                # rail (raise → skip ack → Event Mesh redelivers), not a silent drop.
                requeue_codes = {"CONCURRENCY_LIMIT", "WORKER_SATURATED", "ADMISSION_FULL"}
                if (
                    isinstance(result, dict)
                    and (result.get("error") or {}).get("code") in requeue_codes
                ):
                    raise RuntimeError(
                        f"backpressure: {(result.get('error') or {}).get('code')}, "
                        f"not dispatched (task_id={data.get('task_id')}) — "
                        f"redeliver after lock TTL"
                    )

            execute_uc.task_queue.consume(_on_task)
            _log.info("Event Mesh task consumer started on 'workflow.tasks'")

    async def _sweeper():
        while True:
            await asyncio.sleep(settings.SWEEP_INTERVAL_SECONDS)
            try:
                await asyncio.to_thread(sweep_uc.execute)
            except Exception:
                pass  # logged inside the use case
            if pull_sweep_uc is not None:
                try:
                    await asyncio.to_thread(pull_sweep_uc.execute)
                except Exception:
                    pass
            if stale_sweep_uc is not None:
                try:
                    await asyncio.to_thread(stale_sweep_uc.execute)
                except Exception:
                    pass  # logged inside the use case

    task = None
    if sweep_uc is not None or pull_sweep_uc is not None or stale_sweep_uc is not None:
        task = asyncio.create_task(_sweeper())

    # Start gRPC server alongside FastAPI if configured
    if settings.TRANSPORT_MODE.lower() == "grpc":
        try:
            from app.layer3_adapters.controllers.grpc.server import create_grpc_server
            _grpc_server = await create_grpc_server(
                container, port=settings.GRPC_PORT, max_workers=settings.GRPC_MAX_WORKERS,
            )
            await _grpc_server.start()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Failed to start Executor gRPC server: %s", exc)

    yield

    if _grpc_server is not None:
        await _grpc_server.stop(grace=5)

    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Close message broker if available
    broker = container.get("_broker")
    if broker is not None and hasattr(broker, "close"):
        broker.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Worker Execution Service",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Middleware — response wrapping (only for API routes, ignores docs/swagger)
    api_prefixes = ["/health", "/api/v1"]
    app.add_middleware(ResponseWrapperMiddleware, wrap_prefixes=api_prefixes)

    # Error handlers
    app.add_exception_handler(WesAppError, wes_app_error_handler)
    app.add_exception_handler(Exception, wes_generic_error_handler)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.include_router(task_router, prefix="/api/v1")
    app.include_router(worker_router, prefix="/api/v1")
    app.include_router(heartbeat_router, prefix="/api/v1")
    app.include_router(observability_router, prefix="/api/v1")

    return app


container = build_app_container()

if __name__ == "__main__":
    mode = settings.APP_MODE
    if mode == "REST":
        print("Mode: REST API")
        app = create_app()
        app.state.container = container
        # Keep-alive must outlast the CF gorouter's backend connection reuse
        # window; uvicorn's 5s default surfaces as 502 endpoint_failure (EOF).
        uvicorn.run(app, host="0.0.0.0", port=settings.SERVICE_PORT, timeout_keep_alive=75)

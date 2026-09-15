import asyncio
import inspect
import logging
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from worker_sdk.layer4_frameworks.config.app_config import settings

from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    ExecuteTaskCommand,
)
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.services.result_budget import budget_verdict

_log = logging.getLogger("WorkerSDK")

# SA-1951 — async-result callback delivery. A dropped callback used to hang the
# node forever, so the worker retries transient failures instead of shrugging.
_CALLBACK_MAX_ATTEMPTS = 3
_CALLBACK_TIMEOUT_S = 30.0
_CALLBACK_BACKOFF_S = 1.0
from worker_sdk.layer2_application.features.get_worker_info.use_cases.get_worker_info_usecase import (
    GetWorkerInfoCommand,
    GetWorkerInfoUseCase,
)
from worker_sdk.layer3_adapters.controllers.restful.v1.dtos.base_dto import BaseInputDto, BaseOutputDto


# --- DTOs ---

class ExecuteTaskInputDto(BaseInputDto):
    task_id: str
    action: str
    inputs: dict[str, Any] = {}
    parameters: dict[str, Any] = {}
    correlation_id: Optional[str] = None
    worker_type: Optional[str] = None


class ExecuteAsyncTaskInputDto(BaseInputDto):
    task_id: str
    action: str
    inputs: dict[str, Any] = {}
    parameters: dict[str, Any] = {}
    correlation_id: Optional[str] = None
    callback_url: Optional[str] = None
    worker_type: Optional[str] = None


class ExecuteTaskOutputDto(BaseOutputDto):
    task_id: str
    status: str
    outputs: dict[str, Any] = {}
    error: Optional[str] = None
    duration_ms: float = 0.0
    output_reference: str = ""


class AcceptedOutputDto(BaseOutputDto):
    task_id: str
    accepted: bool = True


class WorkerInfoOutputDto(BaseOutputDto):
    worker_type: str
    version: str
    sdk_version: str = ""
    metadata: dict[str, str] = {}
    input_schema: Optional[Any] = None
    output_schema: Optional[Any] = None
    name: str = ""
    description: str = ""
    node_class: str = "BUSINESS"
    icon: str = "Cog"
    color: str = "#3B82F6"
    tags: list[str] = []
    capabilities: list[dict[str, str]] = []
    ports: dict[str, list[dict[str, Any]]] = {
        "in": [{"id": "in_default", "label": "Input", "required": True, "description": ""}],
        "out": [
            {"id": "success", "label": "Success", "required": True, "description": ""},
            {"id": "failure", "label": "Failure", "required": True, "description": ""},
        ],
    }


# --- Router factory ---

def _resolve_inputs(resolver: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    """Resolve file references in inputs if a resolver is available."""
    if resolver is None or not inputs:
        return inputs
    try:
        resolved = resolver.resolve_inputs(inputs)
        _log.debug("Resolved file references in inputs")
        return resolved
    except Exception as exc:
        _log.error("File reference resolution failed: %s", exc)
        raise


def _post_callback(callback_url: str, payload: dict[str, Any], task_id: str) -> bool:
    """POST an async result to the executor. Return True once it is accepted.

    SA-1951 — this used to be a bare ``client.post(...)`` with no
    ``raise_for_status()``. httpx does not raise on 5xx, so when the executor
    failed to relay the result it answered 500 and the worker treated that as
    success: the result was dropped with no log and no retry, and the node hung
    forever. A worker must never silently discard its own result.

    Retries are for transient failures (connection resets, a broker blip on the
    executor side). A 4xx is the executor telling us the task is not ours to
    complete (unknown/superseded) — retrying that is pointless, so we stop.
    """
    last_error: Exception | str = "unknown"
    for attempt in range(1, _CALLBACK_MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=_CALLBACK_TIMEOUT_S) as client:
                resp = client.post(callback_url, json=payload)
            if resp.is_success:
                return True
            if 400 <= resp.status_code < 500:
                _log.error(
                    "Callback for task %s rejected: HTTP %d %s — not retrying",
                    task_id, resp.status_code, resp.text[:200],
                )
                return False
            last_error = f"HTTP {resp.status_code} {resp.text[:200]}"
        except Exception as exc:  # transport-level failure
            last_error = exc

        _log.warning(
            "Callback for task %s failed (attempt %d/%d): %s",
            task_id, attempt, _CALLBACK_MAX_ATTEMPTS, last_error,
        )
        if attempt < _CALLBACK_MAX_ATTEMPTS:
            time.sleep(_CALLBACK_BACKOFF_S * attempt)

    _log.error(
        "Callback for task %s FAILED after %d attempts: %s — the executor never "
        "took this result; the task will be swept as timed out",
        task_id, _CALLBACK_MAX_ATTEMPTS, last_error,
    )
    return False


def create_router(container: dict) -> APIRouter:
    router = APIRouter()

    # File reference resolver — resolve before any use case sees the inputs
    deps = container.get("_dependencies", {})
    file_ref_resolver = deps.get("file_ref_resolver")
    node_type_registry = deps.get("node_type_registry", {})
    monitor = deps.get("monitor")  # SA-1530: saturation metrics (guarded, optional)

    # --- Feature: get_worker_info ---
    if "get_worker_info_usecase" in container:
        info_uc : GetWorkerInfoUseCase = container["get_worker_info_usecase"]

        @router.get("/info", response_model=WorkerInfoOutputDto)
        def info() -> Any:
            domain_input = GetWorkerInfoCommand()
            domain_output = info_uc.execute(domain_input)
            return WorkerInfoOutputDto.from_dataclass(domain_output)

    # --- Multi-type info endpoint ---
    if node_type_registry:
        from worker_sdk.layer1_domain.value_objects.port import default_task_ports as _default_ports

        @router.get("/info/types")
        def info_types() -> Any:
            return [
                {
                    "worker_type": nt.worker_type,
                    "name": nt.name or nt.worker_type,
                    "description": nt.description,
                    "version": nt.version,
                    "node_class": nt.node_class,
                    "icon": nt.icon,
                    "color": nt.color,
                    "tags": nt.tags,
                    "input_schema": nt.input_schema,
                    "output_schema": nt.output_schema,
                    "capabilities": nt.capabilities or [{"domain": nt.worker_type, "action": "execute"}],
                    "ports": nt.ports or _default_ports(),
                }
                for nt in node_type_registry.values()
            ]

    # --- Feature: execute_task ---
    exec_uc: Optional[ExecuteTaskUseCase] = container.get("execute_task_usecase")

    async def _invoke_node_type_handler(handler: Any, resolved_inputs: Any, parameters: Any) -> Any:
        """Invoke a node type handler, supporting both sync and async handlers.

        Async handlers are awaited directly. **Sync handlers are dispatched
        via ``asyncio.to_thread`` so they don't block the worker's event
        loop** — a sync handler doing CPU work or sync I/O (e.g. a blocking
        HTTP/DB call) would otherwise stall every other in-flight
        ``/api/v1/execute`` request on this worker.
        """
        if inspect.iscoroutinefunction(handler):
            return await handler(resolved_inputs, parameters)
        return await asyncio.to_thread(handler, resolved_inputs, parameters)

    # SA-1905 C2 — worker-side streaming: when enabled, a large list-of-dicts
    # output field is converted to CSV + uploaded here (at the worker), and the
    # inline value is replaced by a file_ref the CP resolves like any stored
    # output. Constructed once; disabled/absent config -> no-op passthrough.
    _output_converter = deps.get("output_converter")
    if _output_converter is None and settings.WORKER_CHUNKED_OUTPUT_ENABLED and settings.FILE_SERVICE_URL:
        from worker_sdk.layer4_frameworks.providers.file_service.http_file_uploader import (
            HttpFileUploader,
        )
        from worker_sdk.layer4_frameworks.providers.data_io.streaming_output_converter import (
            StreamingOutputConverter,
        )
        _output_converter = StreamingOutputConverter(
            HttpFileUploader(settings.FILE_SERVICE_URL),
            enabled=True,
            threshold_bytes=settings.CHUNKING_THRESHOLD_BYTES,
            arrow_sidecar=settings.WORKER_ARROW_SIDECAR_ENABLED,
            stream_scalars=settings.WORKER_STREAM_SCALARS_ENABLED,
            arrow_codec=settings.ARROW_SIDECAR_COMPRESSION,
        )

    def _budget_error(task_id: str, outputs: Any) -> Optional[str]:
        """Weigh the finished result. Returns an error message ONLY when enforcing.

        This runs AFTER ``_maybe_stream_outputs``, so what it measures is what
        the executor will actually be asked to publish — streaming has already
        had its chance at every field it can convert.

        Shadow (the default): log + count, return ``None``. Nothing about the
        outcome changes, which is what makes it safe to ship alongside the fix
        it measures.

        It never raises, and it returns ``None`` on any internal failure. The
        async caller below runs in a background task whose result is delivered
        by an explicit callback; an exception escaping here would kill that task
        with no callback at all and hang the node RUNNING forever — the exact
        failure SA-1951 exists to prevent. Being unable to weigh a result is
        also not evidence that it is too heavy.
        """
        try:
            budget = int(getattr(settings, "RESULT_MAX_OUTPUT_BYTES", 0) or 0)
            # The flag is passed, not read inside the service: layer 2 may not
            # import layer 4 settings (import-linter), and a hard-coded answer
            # there would go stale the day the flag flips.
            message = budget_verdict(
                outputs, budget,
                scalars_streamable=bool(
                    getattr(settings, "WORKER_STREAM_SCALARS_ENABLED", False)
                ),
            )
        except Exception:
            return None
        if message is None:
            return None
        if monitor is not None:
            try:
                monitor.track("worker_result_over_budget", 1)
            except Exception:
                pass
        if getattr(settings, "RESULT_BUDGET_ENFORCE", False):
            _log.error("Result over budget, failing task %s: %s", task_id, message)
            return message
        _log.warning(
            "Result over budget for task %s (shadow mode, publishing anyway): %s",
            task_id, message,
        )
        return None

    async def _maybe_stream_outputs(task_id: str, outputs: Any) -> Any:
        """Replace large list-of-dicts output fields with file_refs (C2). No-op
        when the converter is disabled or the output isn't a dict.

        Never raises: streaming is a best-effort optimization, and the async
        execute path calls this from a background task whose single-type branch
        is not wrapped — a raise here would kill the task with no callback and
        hang the node. On any failure, fall back to the original outputs
        (inline)."""
        if _output_converter is None or not isinstance(outputs, dict):
            return outputs
        try:
            streamed = dict(outputs)
            # SA-1910 — every file_id created below, reported to the control
            # plane so it has the bookkeeping to delete the ones nothing ends up
            # pointing at. The worker cannot decide that itself: whether a ref
            # survives depends on `keep_inline`, `output_binding` and the rest
            # of `complete_task`, none of which it can see.
            #
            # Stated plainly because it is easy to over-read: this covers the
            # callback ARRIVING and the files ending up unreferenced. It does
            # NOT cover the callback being lost — then the list is lost with it,
            # and only a sweeper can find those.
            uploaded: list[str] = []
            for field, value in list(streamed.items()):
                ref = await _output_converter.convert(
                    task_id, field, value, uploaded,
                )
                if ref is not None:
                    streamed[field] = ref
            if uploaded:
                streamed["_uploaded_file_ids"] = uploaded
            return streamed
        except Exception:  # pragma: no cover - defensive; convert() already guards
            return outputs

    if exec_uc is not None or node_type_registry:

        @router.post("/execute", response_model=ExecuteTaskOutputDto)
        async def execute(payload: ExecuteTaskInputDto) -> Any:
            resolved = _resolve_inputs(file_ref_resolver, payload.inputs)

            # Multi-type routing: dispatch to per-type handler if available
            if node_type_registry and payload.worker_type and payload.worker_type in node_type_registry:
                nt = node_type_registry[payload.worker_type]
                if nt.handler:
                    t0 = time.monotonic()
                    try:
                        outputs = await _invoke_node_type_handler(nt.handler, resolved, payload.parameters)
                        outputs = await _maybe_stream_outputs(payload.task_id, outputs)
                        over_budget = _budget_error(payload.task_id, outputs)
                        if over_budget:
                            # No outputs: they are the thing that cannot be
                            # delivered. Shipping them anyway is how the node
                            # ends up RUNNING forever.
                            return ExecuteTaskOutputDto(
                                task_id=payload.task_id,
                                status=TaskStatus.ERROR.value,
                                outputs={},
                                error=over_budget,
                                duration_ms=round((time.monotonic() - t0) * 1000, 2),
                            )
                        duration_ms = (time.monotonic() - t0) * 1000
                        # Use the shared TaskStatus vocab ("success"/"error"),
                        # NOT "COMPLETED"/"FAILED". The result-callback pipeline
                        # (worker-executor HandleWorkerCallbackUseCase) treats
                        # ``status == "success"`` as the ONLY success signal;
                        # any other value is reported to the WCP as WORKER_ERROR.
                        # The single-type path already emits TaskStatus values,
                        # so multi-type must match or every multi-type success
                        # is mis-reported as a failed node (with outputs still
                        # attached — which is exactly how the bug looked live).
                        return ExecuteTaskOutputDto(
                            task_id=payload.task_id,
                            status=TaskStatus.SUCCESS.value,
                            outputs=outputs or {},
                            duration_ms=round(duration_ms, 2),
                        )
                    except Exception as exc:
                        duration_ms = (time.monotonic() - t0) * 1000
                        return ExecuteTaskOutputDto(
                            task_id=payload.task_id,
                            status=TaskStatus.ERROR.value,
                            error=str(exc),
                            duration_ms=round(duration_ms, 2),
                        )

            # Single-type fallback: existing behavior
            if exec_uc is None:
                return ExecuteTaskOutputDto(task_id=payload.task_id, status="FAILED", error="No executor available")
            domain_input = ExecuteTaskCommand(
                task_id=payload.task_id,
                action=payload.action,
                inputs=resolved,
                parameters=payload.parameters,
                correlation_id=payload.correlation_id,
            )
            # ``exec_uc.execute`` is a sync use case; dispatch to a thread so
            # CPU/IO work doesn't block other in-flight requests on the loop.
            domain_output = await asyncio.to_thread(exec_uc.execute, domain_input)
            if getattr(domain_output, "outputs", None):
                domain_output.outputs = await _maybe_stream_outputs(
                    payload.task_id, domain_output.outputs,
                )
                over_budget = _budget_error(payload.task_id, domain_output.outputs)
                if over_budget:
                    domain_output.status = TaskStatus.ERROR
                    domain_output.error = over_budget
                    domain_output.outputs = {}
            return ExecuteTaskOutputDto.from_dataclass(domain_output)

        @router.post("/execute-async", response_model=AcceptedOutputDto, status_code=202)
        async def execute_async(payload: ExecuteAsyncTaskInputDto, background_tasks: BackgroundTasks) -> Any:
            callback_url = payload.callback_url

            # SA-1530 — acceptance gate: claim a slot BEFORE the 202. At
            # capacity respond 429 WORKER_SATURATED (+Retry-After) so the
            # executor REQUEUEs (SA-1531) instead of failing the task.
            # 429, not 503: saturated is busy, not unhealthy — /ready stays
            # true and the platform must not restart the pod.
            concurrency = deps.get("worker_concurrency")
            if concurrency is not None and not concurrency.acquire():
                snap = concurrency.snapshot()
                if monitor is not None:
                    try:
                        monitor.increment("worker_saturation_rejections")
                    except Exception:
                        pass
                return JSONResponse(
                    status_code=429,
                    headers={
                        "Retry-After": str(settings.WORKER_SATURATION_RETRY_AFTER_S),
                    },
                    content={
                        "error": "WORKER_SATURATED",
                        "task_id": payload.task_id,
                        **snap,
                    },
                )
            if concurrency is not None and monitor is not None:
                try:
                    monitor.track("worker_active_tasks", concurrency.snapshot()["active"])
                except Exception:
                    pass

            async def _run_and_callback_async() -> None:
                try:
                    await _run_and_callback_inner()
                finally:
                    # SA-1530: release on success AND error paths, then
                    # refresh the gauge so scale-down signals are accurate.
                    if concurrency is not None:
                        concurrency.release()
                        if monitor is not None:
                            try:
                                monitor.track(
                                    "worker_active_tasks",
                                    concurrency.snapshot()["active"],
                                )
                            except Exception:
                                pass

            async def _run_and_callback_inner() -> None:
                # SA-1943 (async-first) — input resolution (incl. the OOM
                # size-guard raise, and HTTP/connection errors) must produce an
                # ERROR callback, NOT die as a silent unhandled BackgroundTask
                # that hangs the node until its deadline. The sync /execute path
                # surfaces the same failure as a 500 the CP sees; the async path
                # only sees it if we post the callback ourselves.
                try:
                    resolved = _resolve_inputs(file_ref_resolver, payload.inputs)
                except Exception as exc:
                    if callback_url:
                        err_payload = {
                            "task_id": payload.task_id,
                            "status": TaskStatus.ERROR.value,
                            "outputs": {},
                            "error": f"input resolution failed: {exc}",
                            "duration_ms": 0.0,
                            "output_reference": "",
                        }
                        _post_callback(callback_url, err_payload, payload.task_id)
                    return
                result_payload = None

                # Multi-type routing
                if node_type_registry and payload.worker_type and payload.worker_type in node_type_registry:
                    nt = node_type_registry[payload.worker_type]
                    if nt.handler:
                        t0 = time.monotonic()
                        try:
                            outputs = await _invoke_node_type_handler(nt.handler, resolved, payload.parameters)
                            outputs = await _maybe_stream_outputs(payload.task_id, outputs)
                            # Over budget becomes an error CALLBACK, never a
                            # raise: this runs in a background task, so a raise
                            # would leave the executor with no callback at all.
                            over_budget = _budget_error(payload.task_id, outputs)
                            duration_ms = (time.monotonic() - t0) * 1000
                            # "success"/"error" — see the /execute note above:
                            # the callback pipeline only accepts "success".
                            result_payload = {
                                "task_id": payload.task_id,
                                "status": (
                                    TaskStatus.ERROR if over_budget else TaskStatus.SUCCESS
                                ).value,
                                "outputs": {} if over_budget else (outputs or {}),
                                "error": over_budget,
                                "duration_ms": round(duration_ms, 2),
                                "output_reference": "",
                            }
                        except Exception as exc:
                            duration_ms = (time.monotonic() - t0) * 1000
                            result_payload = {
                                "task_id": payload.task_id,
                                "status": TaskStatus.ERROR.value,
                                "outputs": {},
                                "error": str(exc),
                                "duration_ms": round(duration_ms, 2),
                                "output_reference": "",
                            }

                # Single-type fallback
                if result_payload is None and exec_uc is not None:
                    domain_input = ExecuteTaskCommand(
                        task_id=payload.task_id,
                        action=payload.action,
                        inputs=resolved,
                        parameters=payload.parameters,
                        correlation_id=payload.correlation_id,
                    )
                    # Sync use case → run in thread to keep the event loop free.
                    domain_output = await asyncio.to_thread(exec_uc.execute, domain_input)
                    if getattr(domain_output, "outputs", None):
                        domain_output.outputs = await _maybe_stream_outputs(
                            payload.task_id, domain_output.outputs,
                        )
                        # Same as the multi-type branch: degrade to an error
                        # result that still gets CALLED BACK.
                        over_budget = _budget_error(payload.task_id, domain_output.outputs)
                        if over_budget:
                            domain_output.status = TaskStatus.ERROR
                            domain_output.error = over_budget
                            domain_output.outputs = {}
                    status = domain_output.status
                    if hasattr(status, "value"):
                        status = status.value
                    result_payload = {
                        "task_id": domain_output.task_id,
                        "status": status,
                        "outputs": domain_output.outputs,
                        "error": domain_output.error,
                        "duration_ms": domain_output.duration_ms,
                        "output_reference": domain_output.output_reference,
                    }

                if callback_url and result_payload:
                    _post_callback(callback_url, result_payload, payload.task_id)

            background_tasks.add_task(_run_and_callback_async)
            return AcceptedOutputDto(task_id=payload.task_id, accepted=True)

    # --- Feature: worker functions ---
    # ``function_registry`` is an infrastructure dependency, so it lives in
    # ``container["_dependencies"]`` (== ``deps``) — NOT as a top-level
    # container key (those are auto-discovered features). Reading it from
    # ``container`` made this check always False, so ``/functions`` and
    # ``/functions/{name}/invoke`` never mounted for ANY worker (a function-only
    # worker like the gateway-worker was therefore entirely non-invokable).
    # Read from ``deps``, matching ``file_ref_resolver`` / ``node_type_registry``.
    if "function_registry" in deps:
        from worker_sdk.layer2_application.services.function_registry import FunctionRegistry
        func_registry: FunctionRegistry = deps["function_registry"]

        @router.get("/functions")
        async def list_functions() -> Any:
            return [
                {
                    "name": d.name,
                    "description": d.description,
                    "input_schema": d.input_schema,
                    "output_schema": d.output_schema,
                }
                for d in func_registry.list_definitions()
            ]

        @router.post("/functions/{function_name}/invoke")
        async def invoke_function(function_name: str, payload: dict[str, Any] = {}) -> Any:
            params = payload.get("params", payload)
            try:
                result = await func_registry.invoke(function_name, params)
                return {"status": "success", "result": result}
            except KeyError as exc:
                return {"status": "error", "error": str(exc)}
            except Exception as exc:
                return {"status": "error", "error": str(exc)}

    return router

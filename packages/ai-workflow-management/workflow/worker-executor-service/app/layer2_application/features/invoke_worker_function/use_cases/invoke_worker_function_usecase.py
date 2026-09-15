"""Invoke a named function on a registered worker."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore


@dataclass
class InvokeWorkerFunctionInput:
    worker_type: str = ""
    function_name: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class InvokeWorkerFunctionOutput:
    status: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class InvokeWorkerFunctionUseCase:
    """Look up a worker by type, validate the function exists, and invoke it."""

    def __init__(
        self,
        logger: ILogger,
        registry_store: IWorkerRegistryStore,
        app_config: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.registry_store = registry_store
        self._app_config = app_config

    async def execute(self, input: InvokeWorkerFunctionInput) -> InvokeWorkerFunctionOutput:
        self.logger.info(
            "Invoking worker function",
            worker_type=input.worker_type,
            function_name=input.function_name,
        )

        worker = self.registry_store.get_by_worker_type(input.worker_type)
        if not worker:
            return InvokeWorkerFunctionOutput(
                status="error",
                error=f"Worker '{input.worker_type}' not found",
            )

        # Validate function exists in registration
        func_names = {f.name for f in worker.functions}
        if input.function_name not in func_names:
            return InvokeWorkerFunctionOutput(
                status="error",
                error=f"Function '{input.function_name}' not declared by worker '{input.worker_type}'. Available: {sorted(func_names)}",
            )

        if not worker.endpoint_url:
            return InvokeWorkerFunctionOutput(
                status="error",
                error=f"Worker '{input.worker_type}' has no endpoint",
            )

        # Transport follows TRANSPORT_MODE (default "http"). HTTP mirrors task
        # dispatch and targets the worker's /api/v1/functions/{name}/invoke
        # route; "grpc" uses the InvokeFunction RPC.
        transport = (getattr(self._app_config, "TRANSPORT_MODE", "http") or "http").lower()
        if transport == "grpc":
            return self._invoke_grpc(worker, input)
        return await self._invoke_http(worker, input)

    async def _invoke_http(
        self, worker: Any, input: InvokeWorkerFunctionInput,
    ) -> InvokeWorkerFunctionOutput:
        """POST the call to the worker's HTTP function-invoke route.

        The worker returns ``{"status": "success"|"error", "result": {...},
        "error": "..."}``. Gateway-level errors live INSIDE a ``success``
        result, so they pass through faithfully (not collapsed here).
        """
        import httpx

        url = f"{worker.endpoint_url.rstrip('/')}/api/v1/functions/{input.function_name}/invoke"
        timeout_s = float(getattr(self._app_config, "TASK_TIMEOUT_SECONDS", 120))
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                response = await client.post(url, json={"params": input.params})
            response.raise_for_status()
            data = response.json()
            return InvokeWorkerFunctionOutput(
                status=data.get("status", "success"),
                result=data.get("result") or {},
                error=data.get("error") or "",
            )
        except Exception as exc:
            self.logger.error("InvokeWorkerFunction HTTP failed", error=str(exc))
            return InvokeWorkerFunctionOutput(status="error", error=str(exc))

    def _invoke_grpc(
        self, worker: Any, input: InvokeWorkerFunctionInput,
    ) -> InvokeWorkerFunctionOutput:
        """Invoke via the gRPC InvokeFunction RPC (TRANSPORT_MODE=grpc)."""
        try:
            import grpc
            from google.protobuf import struct_pb2
            from google.protobuf.json_format import MessageToDict
            from workflow_proto.v1 import worker_execution_pb2 as pb
            from workflow_proto.v1 import worker_execution_pb2_grpc as pb_grpc

            params_struct = struct_pb2.Struct()
            if input.params:
                params_struct.update(input.params)

            channel = grpc.insecure_channel(worker.endpoint_url)
            stub = pb_grpc.WorkerExecutionServiceStub(channel)
            response = stub.InvokeFunction(
                pb.InvokeFunctionRequest(
                    function_name=input.function_name,
                    params=params_struct,
                ),
                timeout=30.0,
            )
            result = MessageToDict(response.result) if response.HasField("result") else {}
            return InvokeWorkerFunctionOutput(
                status=response.status,
                result=result,
                error=response.error,
            )
        except Exception as exc:
            self.logger.error("InvokeWorkerFunction failed", error=str(exc))
            return InvokeWorkerFunctionOutput(
                status="error",
                error=str(exc),
            )

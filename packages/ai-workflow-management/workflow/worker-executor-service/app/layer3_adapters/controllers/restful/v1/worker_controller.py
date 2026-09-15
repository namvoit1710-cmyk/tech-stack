"""Worker registry endpoints for Worker Execution Service."""

from typing import Any, Optional

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator

from app.layer1_domain.value_objects.node_kind import NodeKind
from app.layer1_domain.value_objects.delivery_mode import DeliveryMode, normalize_delivery_mode

from app.layer2_application.features.register_worker.use_cases.register_worker_usecase import (
    RegisterWorkerUseCase,
    RegisterWorkerInput,
)
from app.layer2_application.features.sdk_register_worker.use_cases.sdk_register_worker_usecase import (
    SdkRegisterWorkerUseCase,
    SdkRegisterWorkerInput,
)
from app.layer2_application.features.list_workers.use_cases.list_workers_usecase import (
    ListWorkersUseCase,
)
from app.layer2_application.features.discover_workers.use_cases.discover_workers_usecase import (
    DiscoverWorkersUseCase,
    DiscoverWorkersInput,
)
from app.layer2_application.features.get_worker.use_cases.get_worker_usecase import (
    GetWorkerUseCase,
    GetWorkerInput,
)
from app.layer2_application.features.get_worker_schema.use_cases.get_worker_schema_usecase import (
    GetWorkerSchemaUseCase,
    GetWorkerSchemaInput,
)
from app.layer2_application.features.deregister_worker.use_cases.deregister_worker_usecase import (
    DeregisterWorkerUseCase,
    DeregisterWorkerInput,
)
from app.layer2_application.features.invoke_worker_function.use_cases.invoke_worker_function_usecase import (
    InvokeWorkerFunctionUseCase,
    InvokeWorkerFunctionInput,
)
from app.layer2_application.features.lease_tasks.use_cases.lease_tasks_usecase import (
    LeaseTasksUseCase,
    LeaseTasksInput,
)
from app.layer2_application.features.queue_stats.use_cases.queue_stats_usecase import (
    QueueStatsUseCase,
    QueueStatsInput,
)
from app.layer2_application.features.list_type_replicas.use_cases.list_type_replicas_usecase import (
    ListTypeReplicasUseCase, ListTypeReplicasInput,
)
from app.layer2_application.features.list_worker_replicas.use_cases.list_worker_replicas_usecase import (
    ListWorkerReplicasUseCase,
)
from app.layer2_application.features.list_worker_apps.use_cases.list_worker_apps_usecase import (
    ListWorkerAppsUseCase,
)
from app.layer2_application.features.list_app_instances.use_cases.list_app_instances_usecase import (
    ListAppInstancesUseCase, ListAppInstancesInput,
)
from .dtos.base_dto import BaseInputDto

router = APIRouter(tags=["workers"])


# --- DTOs ---


class RegisterWorkerInputDto(BaseInputDto):
    name: str
    capabilities: list[dict[str, str]] = []
    endpoint_url: str = ""
    ttl_seconds: int = 60
    # NOTE: no delivery_mode here. The legacy /workers endpoint registers with no
    # worker_type, and pull is (tenant, worker_type)-partitioned, so a pull worker
    # could never resolve/lease via this path. Pull registration goes through the
    # SDK endpoint (/workers/register) or gRPC. (SA-2026)


class SdkRegisterWorkerInputDto(BaseInputDto):
    worker_type: str = Field(..., min_length=1)
    version: str = ""
    spec_version: str = "1.0.0"
    # --- R1 (SA-2048): per-instance identity ---
    # Blank/absent = not supplied -> the executor synthesizes one and returns it in
    # `instance_id` (proto3-additive: an SDK predating SA-2055 keeps working, it just
    # gets a synthesized id and leaves host/pid/started_at blank).
    instance_id: str = ""
    # R14: the deployable this registration belongs to. Proto3-additive exactly
    # like instance_id above: an SDK that sends nothing still registers and the
    # executor derives the app from the endpoint host (WorkerRegistration.
    # __post_init__). Same class of past defect as `kind` below — Pydantic
    # drops any field this model does not declare, so leaving worker_app
    # undeclared would silently discard whatever the SDK sends and every
    # registration would fall back to the derived value regardless of intent.
    worker_app: str = ""
    host: str = ""
    pid: int = 0
    started_at: str = ""
    endpoint: str = ""
    sdk_version: str = ""
    input_schema: Optional[list] = None
    output_schema: Optional[list] = None
    name: str = ""
    description: str = ""
    node_class: str = "BUSINESS"
    # SA-1735: the connector's functional kind (trigger/action/read/logic/human/transform/util).
    # This is the door the SDK actually knocks on — bootstrap.py wires HttpWorkerRegistry, which
    # POSTs here. Pydantic drops any field it does not declare, so without this line every
    # connector node silently registered as the default "action" and the whole (kind, collection)
    # taxonomy collapsed to one section in the palette. Defaults to "action" so an older SDK that
    # sends no kind still registers (proto3-additive).
    # SA-1734 blocker fix: typed as NodeKind (not bare str) so an unknown/invented kind
    # (e.g. "banana") is rejected by pydantic with a 422 instead of silently flowing through
    # to worker_node_provider and reaching the frontend as a bogus category.
    kind: NodeKind = NodeKind.ACTION
    # SA-2026: task delivery mode. A str with a normalizing validator (below) so
    # the HTTP boundary honors the SAME contract as gRPC and the entity: blank /
    # absent -> "push" (proto3-additive back-compat — NOT a 422), a non-empty
    # unknown value (e.g. "poll") -> 422. A bare `DeliveryMode` enum field would
    # have 422'd even on an explicit "".
    delivery_mode: str = DeliveryMode.PUSH.value
    icon: str = "Cog"
    color: str = "#3B82F6"
    tags: list[str] = []
    capabilities: list[dict[str, str]] = []
    ports: dict[str, list[dict[str, Any]]] = {"in": [], "out": []}
    functions: list[dict[str, Any]] = []

    @field_validator("delivery_mode", mode="before")
    @classmethod
    def _normalize_delivery_mode(cls, v: Any) -> str:
        # blank/None -> "push"; unknown non-empty -> ValueError -> 422.
        return normalize_delivery_mode(v).value


class InvokeFunctionInputDto(BaseInputDto):
    params: dict[str, Any] = {}


# --- Dependency Getters ---


def get_register_worker_usecase(request: Request) -> RegisterWorkerUseCase:
    return request.app.state.container["register_worker"]


def get_sdk_register_worker_usecase(request: Request) -> SdkRegisterWorkerUseCase:
    return request.app.state.container["sdk_register_worker"]


def get_list_workers_usecase(request: Request) -> ListWorkersUseCase:
    return request.app.state.container["list_workers"]


def get_get_worker_usecase(request: Request) -> GetWorkerUseCase:
    return request.app.state.container["get_worker"]


def get_get_worker_schema_usecase(request: Request) -> GetWorkerSchemaUseCase:
    return request.app.state.container["get_worker_schema"]


def get_discover_workers_usecase(request: Request) -> DiscoverWorkersUseCase:
    return request.app.state.container["discover_workers"]


def get_deregister_worker_usecase(request: Request) -> DeregisterWorkerUseCase:
    return request.app.state.container["deregister_worker"]


def get_invoke_worker_function_usecase(request: Request) -> InvokeWorkerFunctionUseCase:
    return request.app.state.container["invoke_worker_function"]


def get_lease_tasks_usecase(request: Request) -> LeaseTasksUseCase:
    return request.app.state.container["lease_tasks"]


def get_queue_stats_usecase(request: Request) -> QueueStatsUseCase:
    return request.app.state.container["queue_stats"]


def get_list_type_replicas_usecase(request: Request) -> ListTypeReplicasUseCase:
    return request.app.state.container["list_type_replicas"]


def get_list_worker_replicas_usecase(request: Request) -> ListWorkerReplicasUseCase:
    return request.app.state.container["list_worker_replicas"]


def get_list_worker_apps_usecase(request: Request) -> ListWorkerAppsUseCase:
    return request.app.state.container["list_worker_apps"]


def get_list_app_instances_usecase(request: Request) -> ListAppInstancesUseCase:
    return request.app.state.container["list_app_instances"]


# --- Routes ---
# NOTE: Route order matters — /workers/register and /workers/discover/{capability}
# must come before /workers/{worker_id} to avoid path conflicts.


@router.post("/workers/register")
def sdk_register_worker(
    payload: SdkRegisterWorkerInputDto,
    usecase: SdkRegisterWorkerUseCase = Depends(get_sdk_register_worker_usecase),
) -> Any:
    return usecase.execute(SdkRegisterWorkerInput(**payload.model_dump()))


@router.post("/workers")
def register_worker(
    payload: RegisterWorkerInputDto,
    usecase: RegisterWorkerUseCase = Depends(get_register_worker_usecase),
) -> Any:
    return usecase.execute(RegisterWorkerInput(**payload.model_dump()))


@router.get("/workers/discover/{capability}")
def discover_workers(
    capability: str,
    usecase: DiscoverWorkersUseCase = Depends(get_discover_workers_usecase),
) -> Any:
    return usecase.execute(DiscoverWorkersInput(capability=capability))


@router.get("/workers/type/{worker_type}/functions")
def list_worker_functions(
    worker_type: str,
    usecase: InvokeWorkerFunctionUseCase = Depends(get_invoke_worker_function_usecase),
) -> Any:
    """List functions declared by a worker (looked up by worker_type)."""
    worker = usecase.registry_store.get_by_worker_type(worker_type)
    if not worker:
        return JSONResponse(status_code=404, content={
            "status": "error",
            "error": {"code": "NOT_FOUND", "message": f"Worker '{worker_type}' not found"},
        })
    return [
        {
            "name": f.name,
            "description": f.description,
            "input_schema": f.input_schema,
            "output_schema": f.output_schema,
        }
        for f in worker.functions
    ]


@router.get("/workers/type/{worker_type}/queue-stats")
def queue_stats(
    worker_type: str,
    request: Request,
    usecase: QueueStatsUseCase = Depends(get_queue_stats_usecase),
) -> Any:
    """Pull autoscale / scale-from-zero signal: the leasable backlog for a
    (tenant, worker_type). A KEDA scaler polls this and scales on admitted_depth.
    Tenant comes ONLY from X-Tenant-Id (gateway-injected from the XSUAA subject in
    prod) — NOT a query param, so a caller can't read another tenant's backlog by
    naming it. This endpoint MUST sit behind the api-gateway like the lease route."""
    tenant = request.headers.get("X-Tenant-Id", "")
    return usecase.execute(QueueStatsInput(worker_type=worker_type, tenant=tenant))


@router.get("/workers/type/{worker_type}/replicas")
def list_type_replicas(
    worker_type: str,
    usecase: ListTypeReplicasUseCase = Depends(get_list_type_replicas_usecase),
) -> Any:
    """Per-instance replica detail for one worker_type (SA-2053).

    ALWAYS 200. An unknown or scaled-to-zero worker_type returns [] -- "zero
    replicas" and "does not exist" are the same fact in a soft-state registry, and
    a dashboard polling a scaled-to-zero type must not render an error. Do NOT copy
    the JSONResponse(404) block from get_worker/get_worker_schema below: those
    address a single ROW, where absence really is not-found; this addresses a SET,
    where empty is a valid answer.

    Registered here (with the other literal-segment routes) for coherence, NOT
    because order is load-bearing: this path has four segments and can never match
    @router.get("/workers/{worker_id}")'s single segment, so it is structurally safe
    below the catch-all too (proven by mutation in Task 3's review). Contrast with
    @router.get("/workers/replicas") below, which DOES require its position above
    the catch-all -- see that route's docstring.
    """
    return usecase.execute(ListTypeReplicasInput(worker_type=worker_type))


@router.get("/workers/replicas")
def list_worker_replicas(
    usecase: ListWorkerReplicasUseCase = Depends(get_list_worker_replicas_usecase),
) -> Any:
    """Per-type replica aggregate (SA-2053).

    MUST stay above @router.get("/workers/{worker_id}"): FastAPI matches in
    registration order, so declared below it this becomes
    get_worker(worker_id="replicas") -- a 404 that reads as "no such worker".
    """
    return usecase.execute()


@router.get("/workers/apps")
def list_worker_apps(
    usecase: ListWorkerAppsUseCase = Depends(get_list_worker_apps_usecase),
) -> list[dict]:
    """One row per worker APP (SA-2047 R14).

    MUST stay above @router.get("/workers/{worker_id}") for the same reason
    /workers/replicas does: FastAPI matches in declaration order, and the
    single-segment route would otherwise capture worker_id="apps" and 404.
    """
    return usecase.execute()


@router.get("/workers/apps/{worker_app}/instances")
def list_app_instances(
    worker_app: str,
    usecase: ListAppInstancesUseCase = Depends(get_list_app_instances_usecase),
) -> list[dict]:
    """Every PROCESS of one app. [] for an unknown app: in a soft-state registry
    "no instances" and "does not exist" are the same fact."""
    return usecase.execute(ListAppInstancesInput(worker_app=worker_app))


@router.post("/workers/type/{worker_type}/tasks/lease")
async def lease_tasks(
    worker_type: str,
    request: Request,
    max: int = 1,
    wait: float = 0.0,
    usecase: LeaseTasksUseCase = Depends(get_lease_tasks_usecase),
) -> Any:
    """Pull worker long-polls to lease READY tasks from its (tenant, worker_type)
    partition. Tenant comes from the XSUAA subject in B5; B3 reads a header,
    defaulting to the single un-partitioned namespace. Returns a (possibly empty)
    list of leased tasks; the response envelope wraps it as {status:ok, data:[...]}.
    """
    tenant = request.headers.get("X-Tenant-Id", "")
    # SA-2424: the caller identifies its instance so a per-instance-pinned task is
    # leased only by that instance. Header (X-Instance-Id) OR ?instance_id= query.
    instance_id = request.headers.get("X-Instance-Id", "") or request.query_params.get("instance_id", "")
    return await usecase.execute(LeaseTasksInput(
        worker_type=worker_type, tenant=tenant, max_n=max, wait_seconds=wait,
        instance_id=instance_id,
    ))


@router.post("/workers/type/{worker_type}/functions/{function_name}/invoke")
async def invoke_worker_function(
    worker_type: str,
    function_name: str,
    payload: InvokeFunctionInputDto,
    usecase: InvokeWorkerFunctionUseCase = Depends(get_invoke_worker_function_usecase),
) -> Any:
    """Invoke a named function on a worker."""
    result = await usecase.execute(InvokeWorkerFunctionInput(
        worker_type=worker_type,
        function_name=function_name,
        params=payload.params,
    ))
    if result.status == "error":
        return JSONResponse(status_code=400, content={
            "status": "error",
            "error": {"code": "FUNCTION_ERROR", "message": result.error},
        })
    return {"status": result.status, "result": result.result}


@router.get("/workers/{worker_id}/schema")
def get_worker_schema(
    worker_id: str,
    usecase: GetWorkerSchemaUseCase = Depends(get_get_worker_schema_usecase),
) -> Any:
    result = usecase.execute(GetWorkerSchemaInput(worker_id=worker_id))
    if isinstance(result, dict) and "error" in result and len(result) == 1:
        return JSONResponse(status_code=404, content={
            "status": "error",
            "error": {"code": "NOT_FOUND", "message": result["error"]},
        })
    return result


@router.get("/workers")
def list_workers(
    usecase: ListWorkersUseCase = Depends(get_list_workers_usecase),
) -> Any:
    return usecase.execute()


@router.get("/workers/{worker_id}")
def get_worker(
    worker_id: str,
    usecase: GetWorkerUseCase = Depends(get_get_worker_usecase),
) -> Any:
    result = usecase.execute(GetWorkerInput(worker_id=worker_id))
    if isinstance(result, dict) and "error" in result and len(result) == 1:
        return JSONResponse(status_code=404, content={
            "status": "error",
            "error": {"code": "NOT_FOUND", "message": result["error"]},
        })
    return result


@router.post("/workers/{worker_id}/deregister")
def sdk_deregister_worker(
    worker_id: str,
    usecase: DeregisterWorkerUseCase = Depends(get_deregister_worker_usecase),
) -> Any:
    return usecase.execute(DeregisterWorkerInput(worker_id=worker_id))


@router.delete("/workers/{worker_id}")
def deregister_worker(
    worker_id: str,
    usecase: DeregisterWorkerUseCase = Depends(get_deregister_worker_usecase),
) -> Any:
    return usecase.execute(DeregisterWorkerInput(worker_id=worker_id))

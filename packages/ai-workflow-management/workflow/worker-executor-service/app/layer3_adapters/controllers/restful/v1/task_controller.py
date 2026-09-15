"""Task-related endpoints for Worker Execution Service."""

from typing import Any

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse

from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    ExecuteTaskInput,
)
from app.layer2_application.features.handle_worker_callback.use_cases.handle_worker_callback_usecase import (
    HandleWorkerCallbackUseCase,
    HandleWorkerCallbackInput,
)
from app.layer2_application.features.report_task_result.use_cases.report_task_result_usecase import (
    ReportTaskResultUseCase,
    ReportTaskResultInput,
)
from app.layer2_application.features.route_task.use_cases.route_task_usecase import (
    RouteTaskUseCase,
    RouteTaskInput,
)
from app.layer2_application.features.renew_lease.use_cases.renew_lease_usecase import (
    RenewLeaseUseCase,
    RenewLeaseInput,
)
from app.layer2_application.features.purge_run_tasks.use_cases.purge_run_tasks_usecase import (
    PurgeRunTasksUseCase,
    PurgeRunTasksInput,
)
from .dtos.base_dto import BaseInputDto

router = APIRouter(tags=["tasks"])


# --- DTOs ---


class ExecuteTaskInputDto(BaseInputDto):
    task_id: str
    run_id: str = ""
    node_id: str = ""
    payload: dict[str, Any] = {}
    input: dict[str, Any] = {}
    worker_type: str = ""
    spec_version: str = ""  # SA-1490 Phase 1: accepted + plumbed, not yet honored
    node_config: dict[str, Any] = {}
    idempotency_key: str = ""
    tenant: str = ""  # SA-2030: run tenant → pull partition + callback binding
    run_generation: str = ""  # SA-2032: WCP run generation → stale-run fencing
    # SA-2424: per-node/whole-flow instance pin from WCP dispatch. MUST be declared
    # here or Pydantic drops it from the body → the task enqueues/dispatches
    # unpinned (routing silently ignored). Threads into ExecuteTaskInput below.
    target_instance_id: str = ""
    target_host: str = ""


class WorkerCallbackInputDto(BaseInputDto):
    task_id: str
    status: str = ""
    outputs: dict[str, Any] = {}
    error: str | None = None
    duration_ms: float = 0.0
    output_reference: str = ""
    # SA-2030: pull callback carries its worker_type for the triple-binding check.
    # Empty/absent on a push callback (harmless — the pull branch isn't taken).
    worker_type: str = ""


class RouteTaskInputDto(BaseInputDto):
    task_id: str
    capability: str


# --- Dependency Getters ---


def get_execute_task_usecase(request: Request) -> ExecuteTaskUseCase:
    return request.app.state.container["execute_task"]


def get_handle_worker_callback_usecase(request: Request) -> HandleWorkerCallbackUseCase:
    return request.app.state.container["handle_worker_callback"]


def get_report_task_result_usecase(request: Request) -> ReportTaskResultUseCase:
    return request.app.state.container["report_task_result"]


def get_route_task_usecase(request: Request) -> RouteTaskUseCase:
    return request.app.state.container["route_task"]


def get_renew_lease_usecase(request: Request) -> RenewLeaseUseCase:
    return request.app.state.container["renew_lease"]


def get_purge_run_tasks_usecase(request: Request) -> PurgeRunTasksUseCase:
    return request.app.state.container["purge_run_tasks"]


# --- Routes ---


@router.post("/tasks/execute")
def execute_task(
    payload: ExecuteTaskInputDto,
    usecase: ExecuteTaskUseCase = Depends(get_execute_task_usecase),
) -> Any:
    return usecase.execute(ExecuteTaskInput(**payload.model_dump()))


@router.post("/tasks/callback")
def handle_worker_callback(
    payload: WorkerCallbackInputDto,
    request: Request,
    usecase: HandleWorkerCallbackUseCase = Depends(get_handle_worker_callback_usecase),
) -> Any:
    # Pull triple-binding credentials are HEADER-only (never the URL, and the
    # lease token is not trusted from the body): X-Lease-Token + X-Tenant-Id.
    # Absent on a push callback → empty → push path unchanged.
    data = payload.model_dump()
    data["lease_token"] = request.headers.get("X-Lease-Token", "")
    data["tenant"] = request.headers.get("X-Tenant-Id", "")
    result = usecase.execute(HandleWorkerCallbackInput(**data))
    # Any lease-credentialed callback that did NOT complete a task → the SAME
    # opaque 409, whether the lease was bad/expired/cross-tenant/cross-type OR the
    # task was already gone/never a live pull task. Uniform response closes the
    # "is this task_id a live leased pull task?" existence/state oracle. A push
    # callback carries no X-Lease-Token, so it keeps its existing 200/accepted=false.
    if data["lease_token"] and not result.get("accepted"):
        return JSONResponse(status_code=409, content={
            "status": "error",
            "error": {"code": "TASK_SUPERSEDED", "message": "Lease no longer valid"},
        })
    return result


@router.post("/tasks/{task_id}/lease/renew")
def renew_lease(
    task_id: str,
    request: Request,
    usecase: RenewLeaseUseCase = Depends(get_renew_lease_usecase),
) -> Any:
    """Pull worker extends a still-running task's lease. Lease token is header-only
    (never in the URL). 409 TASK_SUPERSEDED if the lease no longer matches."""
    lease_token = request.headers.get("X-Lease-Token", "")
    result = usecase.execute(RenewLeaseInput(task_id=task_id, lease_token=lease_token))
    if not result.get("ok"):
        return JSONResponse(status_code=409, content={
            "status": "error",
            "error": {"code": "TASK_SUPERSEDED", "message": "Lease no longer valid"},
        })
    return {"task_id": task_id, "deadline": result["deadline"]}


@router.post("/runs/{run_id}/tasks/purge")
def purge_run_tasks(
    run_id: str,
    usecase: PurgeRunTasksUseCase = Depends(get_purge_run_tasks_usecase),
) -> Any:
    """WCP purges a run's pull tasks on cancel/reset (SA-2032) — after this a
    stale-run task can neither be leased nor completed."""
    return usecase.execute(PurgeRunTasksInput(run_id=run_id))


@router.post("/tasks/report")
def report_task_result(
    payload: dict,
    usecase: ReportTaskResultUseCase = Depends(get_report_task_result_usecase),
) -> Any:
    return usecase.execute(ReportTaskResultInput(data=payload))


@router.post("/tasks/route")
def route_task(
    payload: RouteTaskInputDto,
    usecase: RouteTaskUseCase = Depends(get_route_task_usecase),
) -> Any:
    result = usecase.execute(RouteTaskInput(**payload.model_dump()))
    if isinstance(result, dict) and "error" in result and len(result) == 1:
        return JSONResponse(status_code=404, content={
            "status": "error",
            "error": {"code": "NOT_FOUND", "message": result["error"]},
        })
    return result

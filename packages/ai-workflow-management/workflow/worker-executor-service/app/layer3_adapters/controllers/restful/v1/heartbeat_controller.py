"""Heartbeat-related endpoints for Worker Execution Service."""

from fastapi import APIRouter, Request, Depends

from app.layer2_application.features.worker_heartbeat.use_cases.worker_heartbeat_usecase import (
    WorkerHeartbeatUseCase,
    WorkerHeartbeatInput,
)
from app.layer2_application.features.report_health.use_cases.report_health_usecase import (
    ReportHealthUseCase,
    ReportHealthInput,
)
from app.layer2_application.features.start_heartbeat_loop.use_cases.start_heartbeat_loop_usecase import (
    StartHeartbeatLoopUseCase,
    StartHeartbeatLoopInput,
)
from .dtos.base_dto import BaseInputDto
from typing import Any

router = APIRouter(tags=["heartbeat"])


# --- DTOs ---


class HealthReportInputDto(BaseInputDto):
    worker_id: str
    healthy: bool = True


class WorkerHeartbeatInputDto(BaseInputDto):
    status: str = "healthy"


# --- Dependency Getters ---


def get_worker_heartbeat_usecase(request: Request) -> WorkerHeartbeatUseCase:
    return request.app.state.container["worker_heartbeat"]


def get_report_health_usecase(request: Request) -> ReportHealthUseCase:
    return request.app.state.container["report_health"]


def get_start_heartbeat_loop_usecase(request: Request) -> StartHeartbeatLoopUseCase:
    return request.app.state.container["start_heartbeat_loop"]


# --- Routes ---


@router.post("/workers/{worker_id}/heartbeat")
def worker_heartbeat(
    worker_id: str,
    payload: WorkerHeartbeatInputDto | None = None,
    usecase: WorkerHeartbeatUseCase = Depends(get_worker_heartbeat_usecase),
) -> Any:
    status = payload.status if payload else "healthy"
    return usecase.execute(WorkerHeartbeatInput(worker_id=worker_id, status=status))


@router.post("/heartbeat/report")
def report_health(
    payload: HealthReportInputDto,
    usecase: ReportHealthUseCase = Depends(get_report_health_usecase),
) -> Any:
    return usecase.execute(ReportHealthInput(**payload.model_dump()))


@router.post("/heartbeat/start/{worker_id}")
def start_heartbeat(
    worker_id: str,
    usecase: StartHeartbeatLoopUseCase = Depends(get_start_heartbeat_loop_usecase),
) -> Any:
    return usecase.execute(StartHeartbeatLoopInput(worker_id=worker_id))

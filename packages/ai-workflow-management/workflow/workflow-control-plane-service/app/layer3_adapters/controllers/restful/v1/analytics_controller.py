"""SA-2597 — Dashboard analytics API.

Mounted under ``/api/v1/analytics``. Powers the workflow-management Dashboard tab
(SA-2590). Guarded by the auth-enforcement middleware like every ``/api/v1/*``
route. Tenant is taken from the ``x-tenant-id`` header (the API gateway injects it
from the verified identity); when absent, aggregates across the deployment.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.layer2_application.features.dashboard_analytics.use_cases.dashboard_analytics_use_case import (
    DashboardAnalyticsUseCase,
)

router = APIRouter()


def _analytics_uc(request: Request) -> DashboardAnalyticsUseCase:
    return request.app.state.container["dashboard_analytics"]


def _tenant(request: Request) -> str | None:
    return request.headers.get("x-tenant-id") or None


@router.get("/overview")
async def overview(
    request: Request,
    range: str = "30d",
    usecase: DashboardAnalyticsUseCase = Depends(_analytics_uc),
) -> Any:
    return await usecase.overview(range, _tenant(request))


@router.get("/workflows")
async def workflows(
    request: Request,
    range: str = "90d",
    owner: str = "",
    q: str = "",
    status: str = "all",
    page: int = 1,
    pageSize: int = 10,
    usecase: DashboardAnalyticsUseCase = Depends(_analytics_uc),
) -> Any:
    return await usecase.workflows(
        range, _tenant(request), owner or None, q or None, status or None, page, pageSize)

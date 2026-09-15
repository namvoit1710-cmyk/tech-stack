"""SA-2597 — Dashboard analytics feature (auto-discovered as ``dashboard_analytics``)."""
from app.layer2_application.features.dashboard_analytics.use_cases.dashboard_analytics_use_case import (
    DashboardAnalyticsUseCase,
)

__all__ = ["DashboardAnalyticsUseCase"]

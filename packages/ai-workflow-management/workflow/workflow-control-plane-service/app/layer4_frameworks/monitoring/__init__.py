"""Monitoring infrastructure factory."""
from __future__ import annotations

from app.layer2_application.interfaces.monitor_interface import IMonitor
from typing import Any


def create_monitor(settings: Any) -> IMonitor:
    from app.layer4_frameworks.monitoring.prometheus import PrometheusMonitor
    return PrometheusMonitor()

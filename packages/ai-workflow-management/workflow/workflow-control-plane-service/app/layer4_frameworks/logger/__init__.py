"""Logging infrastructure factory."""
from __future__ import annotations

from app.layer2_application.interfaces.logger_interface import ILogger
from typing import Any


def create_logger(settings: Any) -> ILogger:
    from app.layer4_frameworks.logger.app_logger import StandardLogger
    return StandardLogger()

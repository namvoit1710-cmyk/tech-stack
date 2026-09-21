"""Tests for executor IOC container wiring."""

from __future__ import annotations

from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.dependency.ioc_container import (
    build_logger,
    build_workflow_control_plane_client,
    build_status_publisher,
)


class _CapturedPublisher:
    async def publish(self, topic: str, message: dict, key: str | None = None) -> None:
        return None


def test_build_status_publisher_uses_executor_topic():
    publisher = _CapturedPublisher()

    status_publisher = build_status_publisher(publisher)

    assert status_publisher._topic == settings.STATUS_PUBLISH_TOPIC
    assert status_publisher._publisher is publisher


def test_build_logger_returns_standard_logger_like_instance():
    logger = build_logger()

    assert hasattr(logger, "info")
    assert hasattr(logger, "warning")
    assert hasattr(logger, "error")
    assert hasattr(logger, "debug")


def test_build_workflow_control_plane_client_returns_none_when_url_missing(monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_CONTROL_PLANE_URL", "")

    assert build_workflow_control_plane_client() is None


def test_build_workflow_control_plane_client_uses_configured_url(monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_CONTROL_PLANE_URL", "http://workflow.local")
    monkeypatch.setattr(settings, "WORKFLOW_CONTROL_PLANE_TIMEOUT_SECONDS", 7.5)

    client = build_workflow_control_plane_client()

    assert client is not None
    assert client._base_url == "http://workflow.local/api/v1"

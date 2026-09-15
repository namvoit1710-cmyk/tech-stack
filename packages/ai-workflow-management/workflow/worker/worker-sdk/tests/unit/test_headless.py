import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from worker_sdk.layer3_adapters.controllers.worker_headless import run_headless_worker


class DummyLogger:
    def __init__(self):
        self.events = []

    def info(self, message: str, **kwargs):
        self.events.append(("info", message))

    def error(self, message: str, **kwargs):
        self.events.append(("error", message))

    def warning(self, message: str, **kwargs):
        self.events.append(("warning", message))

    def debug(self, message: str, **kwargs):
        self.events.append(("debug", message))


@pytest.mark.asyncio
async def test_headless_worker_registers_and_heartbeats(monkeypatch):
    """Headless worker registers, runs heartbeat, then deregisters on cancel."""
    registry = AsyncMock()
    registry.register = AsyncMock(return_value="w-1")
    registry.heartbeat = AsyncMock()
    registry.deregister = AsyncMock()
    registry.close = AsyncMock()

    logger = DummyLogger()

    container = {
        "_dependencies": {
            "worker_registry": registry,
            "logger": logger,
        }
    }

    # Patch HEARTBEAT_INTERVAL_SECONDS to a very small value
    monkeypatch.setattr(
        "worker_sdk.layer3_adapters.controllers.worker_headless.settings",
        MagicMock(
            WORKER_TYPE="test",
            WORKER_VERSION="1.0",
            SDK_VERSION="1.0.0",
            HEARTBEAT_INTERVAL_SECONDS=0.01,
        ),
    )

    task = asyncio.create_task(run_headless_worker(container))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    registry.register.assert_called_once()
    assert registry.heartbeat.call_count >= 1
    registry.deregister.assert_called_once_with("w-1")
    registry.close.assert_called_once()


@pytest.mark.asyncio
async def test_headless_worker_no_registry(monkeypatch):
    """Headless worker works without a registry."""
    logger = DummyLogger()

    container = {
        "_dependencies": {
            "logger": logger,
        }
    }

    monkeypatch.setattr(
        "worker_sdk.layer3_adapters.controllers.worker_headless.settings",
        MagicMock(
            WORKER_TYPE="test",
            WORKER_VERSION="1.0",
            SDK_VERSION="1.0.0",
            HEARTBEAT_INTERVAL_SECONDS=0.01,
        ),
    )

    task = asyncio.create_task(run_headless_worker(container))
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert any("running" in msg.lower() for _, msg in logger.events)


@pytest.mark.asyncio
async def test_headless_worker_registration_failure(monkeypatch):
    """Headless worker continues even if registration fails."""
    registry = AsyncMock()
    registry.register = AsyncMock(side_effect=Exception("conn refused"))
    registry.close = AsyncMock()

    logger = DummyLogger()

    container = {
        "_dependencies": {
            "worker_registry": registry,
            "logger": logger,
        }
    }

    monkeypatch.setattr(
        "worker_sdk.layer3_adapters.controllers.worker_headless.settings",
        MagicMock(
            WORKER_TYPE="test",
            WORKER_VERSION="1.0",
            SDK_VERSION="1.0.0",
            HEARTBEAT_INTERVAL_SECONDS=0.01,
        ),
    )

    task = asyncio.create_task(run_headless_worker(container))
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert any("error" == level for level, _ in logger.events)


@pytest.mark.asyncio
async def test_headless_worker_heartbeat_failure(monkeypatch):
    """Headless worker handles heartbeat failures gracefully."""
    registry = AsyncMock()
    registry.register = AsyncMock(return_value="w-2")
    registry.heartbeat = AsyncMock(side_effect=Exception("timeout"))
    registry.deregister = AsyncMock()
    registry.close = AsyncMock()

    logger = DummyLogger()

    container = {
        "_dependencies": {
            "worker_registry": registry,
            "logger": logger,
        }
    }

    monkeypatch.setattr(
        "worker_sdk.layer3_adapters.controllers.worker_headless.settings",
        MagicMock(
            WORKER_TYPE="test",
            WORKER_VERSION="1.0",
            SDK_VERSION="1.0.0",
            HEARTBEAT_INTERVAL_SECONDS=0.01,
        ),
    )

    task = asyncio.create_task(run_headless_worker(container))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert any("error" == level for level, _ in logger.events)


@pytest.mark.asyncio
async def test_headless_worker_no_logger(monkeypatch):
    """Headless worker works without a logger."""
    container = {
        "_dependencies": {}
    }

    monkeypatch.setattr(
        "worker_sdk.layer3_adapters.controllers.worker_headless.settings",
        MagicMock(
            WORKER_TYPE="test",
            WORKER_VERSION="1.0",
            SDK_VERSION="1.0.0",
            HEARTBEAT_INTERVAL_SECONDS=0.01,
        ),
    )

    task = asyncio.create_task(run_headless_worker(container))
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_headless_worker_deregister_failure(monkeypatch):
    """Headless worker handles deregistration failure gracefully."""
    registry = AsyncMock()
    registry.register = AsyncMock(return_value="w-3")
    registry.heartbeat = AsyncMock()
    registry.deregister = AsyncMock(side_effect=Exception("dereg failed"))
    registry.close = AsyncMock()

    logger = DummyLogger()

    container = {
        "_dependencies": {
            "worker_registry": registry,
            "logger": logger,
        }
    }

    monkeypatch.setattr(
        "worker_sdk.layer3_adapters.controllers.worker_headless.settings",
        MagicMock(
            WORKER_TYPE="test",
            WORKER_VERSION="1.0",
            SDK_VERSION="1.0.0",
            HEARTBEAT_INTERVAL_SECONDS=0.01,
        ),
    )

    task = asyncio.create_task(run_headless_worker(container))
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Should not crash, deregistration failure is logged
    assert any("error" == level for level, _ in logger.events)

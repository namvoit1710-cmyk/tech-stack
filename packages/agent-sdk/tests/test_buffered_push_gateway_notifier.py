from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


class _RecordingNotifier:
    def __init__(self, *, block_first_calls: int = 0) -> None:
        self.calls: list[tuple[str, object, bool]] = []
        self.closed = False
        self._block_first_calls = block_first_calls
        self._release = asyncio.Event()
        self.first_call_started = asyncio.Event()

    async def send_notification(
        self, key: str, data: object, *, is_final: bool = True
    ) -> None:
        self.calls.append((key, data, is_final))
        self.first_call_started.set()
        if len(self.calls) <= self._block_first_calls:
            await self._release.wait()

    async def close(self) -> None:
        self.closed = True

    def release(self) -> None:
        self._release.set()


@pytest.mark.asyncio
async def test_first_send_lazily_starts_worker():
    from agent_sdk.layer4_frameworks.messaging.buffered_push_gateway_notifier import (
        BufferedPushGatewayNotifier,
    )

    inner = _RecordingNotifier()
    notifier = BufferedPushGatewayNotifier(
        inner=inner,
        max_size=4,
        enqueue_timeout_ms=50,
        drain_timeout_seconds=1,
    )

    assert notifier._worker_task is None

    await notifier.send_notification("conv-1", {"type": "started"}, is_final=False)
    await asyncio.wait_for(inner.first_call_started.wait(), timeout=1)

    assert notifier._worker_task is not None

    await notifier.close()
    assert inner.closed is True


@pytest.mark.asyncio
async def test_buffered_send_calls_inner_asynchronously():
    from agent_sdk.layer4_frameworks.messaging.buffered_push_gateway_notifier import (
        BufferedPushGatewayNotifier,
    )

    inner = _RecordingNotifier(block_first_calls=1)
    notifier = BufferedPushGatewayNotifier(
        inner=inner,
        max_size=4,
        enqueue_timeout_ms=50,
        drain_timeout_seconds=1,
    )

    await asyncio.wait_for(
        notifier.send_notification("conv-async", {"step": 1}, is_final=False),
        timeout=1,
    )
    await asyncio.wait_for(inner.first_call_started.wait(), timeout=1)

    assert inner.calls == [("conv-async", {"step": 1}, False)]

    inner.release()
    await notifier.close()


@pytest.mark.asyncio
async def test_full_queue_enqueue_timeout_falls_back_to_inline_delivery():
    from agent_sdk.layer4_frameworks.messaging.buffered_push_gateway_notifier import (
        BufferedPushGatewayNotifier,
    )

    inner = _RecordingNotifier(block_first_calls=1)
    notifier = BufferedPushGatewayNotifier(
        inner=inner,
        max_size=1,
        enqueue_timeout_ms=10,
        drain_timeout_seconds=1,
    )

    await notifier.send_notification("conv-1", {"step": 1}, is_final=False)
    await asyncio.wait_for(inner.first_call_started.wait(), timeout=1)
    await notifier.send_notification("conv-2", {"step": 2}, is_final=False)
    await notifier.send_notification("conv-3", {"step": 3}, is_final=True)

    assert [call[0] for call in inner.calls[:2]] == ["conv-1", "conv-3"]

    inner.release()
    await notifier.close()
    assert [call[0] for call in inner.calls] == ["conv-1", "conv-3", "conv-2"]


@pytest.mark.asyncio
async def test_close_drains_queued_notifications_before_closing_inner():
    from agent_sdk.layer4_frameworks.messaging.buffered_push_gateway_notifier import (
        BufferedPushGatewayNotifier,
    )

    inner = _RecordingNotifier()
    notifier = BufferedPushGatewayNotifier(
        inner=inner,
        max_size=4,
        enqueue_timeout_ms=50,
        drain_timeout_seconds=1,
    )

    await notifier.send_notification("conv-1", {"step": 1}, is_final=False)
    await notifier.send_notification("conv-2", {"step": 2}, is_final=True)
    await notifier.close()

    assert [call[0] for call in inner.calls] == ["conv-1", "conv-2"]
    assert inner.closed is True


@pytest.mark.asyncio
async def test_dead_worker_is_recreated_on_next_send():
    from agent_sdk.layer4_frameworks.messaging.buffered_push_gateway_notifier import (
        BufferedPushGatewayNotifier,
    )

    inner = _RecordingNotifier()
    notifier = BufferedPushGatewayNotifier(
        inner=inner,
        max_size=4,
        enqueue_timeout_ms=50,
        drain_timeout_seconds=1,
    )

    stale_worker = asyncio.create_task(asyncio.sleep(0))
    await stale_worker
    notifier._worker_task = stale_worker

    await notifier.send_notification("conv-1", {"step": 1}, is_final=False)

    assert notifier._worker_task is not stale_worker
    await notifier.close()


def test_factory_wraps_real_notifier_in_buffered_wrapper_when_enabled():
    from agent_sdk.layer4_frameworks.messaging.buffered_push_gateway_notifier import (
        BufferedPushGatewayNotifier,
    )
    from agent_sdk.layer4_frameworks.messaging.factory import (
        build_push_gateway_notifier,
    )

    settings = SimpleNamespace(
        INFRA_MODE="local",
        MESSAGING_MODE="",
        PUSH_GATEWAY_TRANSPORT="http",
        PUSH_GATEWAY_URL="http://localhost:8000",
        PUSH_GATEWAY_GRPC_TARGET="",
        PUSH_GATEWAY_BUFFER_ENABLED=True,
        PUSH_GATEWAY_BUFFER_MAX_SIZE=8,
        PUSH_GATEWAY_BUFFER_ENQUEUE_TIMEOUT_MS=25,
        PUSH_GATEWAY_BUFFER_DRAIN_TIMEOUT_SECONDS=2,
    )

    notifier = build_push_gateway_notifier(settings)

    assert isinstance(notifier, BufferedPushGatewayNotifier)


def test_factory_skips_buffer_wrapper_for_mock_mode():
    from agent_sdk.layer4_frameworks.messaging.console_push_gateway_notifier import (
        ConsolePushGatewayNotifier,
    )
    from agent_sdk.layer4_frameworks.messaging.factory import (
        build_push_gateway_notifier,
    )

    settings = SimpleNamespace(
        INFRA_MODE="mock",
        MESSAGING_MODE="",
        PUSH_GATEWAY_TRANSPORT="http",
        PUSH_GATEWAY_URL="http://localhost:8000",
        PUSH_GATEWAY_GRPC_TARGET="",
        PUSH_GATEWAY_BUFFER_ENABLED=True,
    )

    notifier = build_push_gateway_notifier(settings)

    assert isinstance(notifier, ConsolePushGatewayNotifier)

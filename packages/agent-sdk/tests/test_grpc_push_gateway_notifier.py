"""Unit tests for GrpcPushGatewayNotifier (SDK)."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import agent_sdk.layer4_frameworks.messaging.grpc_push_gateway_notifier as notifier_module
from agent_sdk.layer4_frameworks.messaging.grpc_push_gateway_notifier import (
    GrpcPushGatewayNotifier,
)


def _install_grpc_test_doubles(monkeypatch, *, send_data_side_effect=None):
    send_data = AsyncMock(side_effect=send_data_side_effect)
    if send_data_side_effect is None:
        send_data.return_value = SimpleNamespace(
            success=True,
            message="ok",
            chunk_index=0,
            is_final=True,
        )

    stub = SimpleNamespace(SendData=send_data)
    stub_factory = MagicMock(return_value=stub)
    channel = AsyncMock()
    insecure_channel = MagicMock(return_value=channel)

    monkeypatch.setattr(
        notifier_module,
        "grpc",
        SimpleNamespace(aio=SimpleNamespace(insecure_channel=insecure_channel)),
    )
    monkeypatch.setattr(
        notifier_module.push_gateway_pb2_grpc,
        "PushGatewayStub",
        stub_factory,
    )

    return channel, insecure_channel, send_data, stub_factory


@pytest.mark.asyncio
async def test_send_notification_maps_payload_to_unary_request(monkeypatch):
    notifier = GrpcPushGatewayNotifier("push-gateway:50051")
    channel, insecure_channel, send_data, stub_factory = _install_grpc_test_doubles(
        monkeypatch
    )

    await notifier.send_notification(
        key="conv-1",
        data={"type": "response", "content": "hi"},
        is_final=False,
    )

    insecure_channel.assert_called_once_with("push-gateway:50051")
    stub_factory.assert_called_once_with(channel)

    request = send_data.await_args.args[0]
    assert isinstance(request, notifier_module.push_gateway_pb2.SendDataRequest)
    assert request.key == "conv-1"
    assert request.is_final is False
    assert json.loads(request.data) == {"type": "response", "content": "hi"}


@pytest.mark.asyncio
async def test_dict_serialization_matches_http_notifier(monkeypatch):
    notifier = GrpcPushGatewayNotifier("push-gateway:50051")
    _, _, send_data, _ = _install_grpc_test_doubles(monkeypatch)

    payload = {"nested": {"key": "value"}, "list": [1, 2, 3]}
    await notifier.send_notification(key="conv-2", data=payload)

    request = send_data.await_args.args[0]
    assert json.loads(request.data) == payload


@pytest.mark.asyncio
async def test_grpc_error_logged_no_raise(monkeypatch, caplog):
    notifier = GrpcPushGatewayNotifier("push-gateway:50051")
    _install_grpc_test_doubles(
        monkeypatch,
        send_data_side_effect=RuntimeError("grpc unavailable"),
    )

    with caplog.at_level(logging.WARNING):
        await notifier.send_notification(key="conv-3", data={"type": "test"})

    assert "Push gateway notification failed for key=conv-3" in caplog.text


@pytest.mark.asyncio
async def test_channel_and_stub_created_lazily_and_reused(monkeypatch):
    notifier = GrpcPushGatewayNotifier("push-gateway:50051")
    channel, insecure_channel, send_data, stub_factory = _install_grpc_test_doubles(
        monkeypatch
    )

    assert notifier._channel is None
    assert notifier._stub is None

    await notifier.send_notification(key="conv-4", data="first")
    await notifier.send_notification(key="conv-5", data="second")

    insecure_channel.assert_called_once_with("push-gateway:50051")
    stub_factory.assert_called_once_with(channel)
    assert send_data.await_count == 2


@pytest.mark.asyncio
async def test_close_shuts_down_channel_and_clears_state(monkeypatch):
    notifier = GrpcPushGatewayNotifier("push-gateway:50051")
    channel, _, _, _ = _install_grpc_test_doubles(monkeypatch)

    await notifier.send_notification(key="conv-6", data="hello")
    await notifier.close()

    channel.close.assert_awaited_once()
    assert notifier._channel is None
    assert notifier._stub is None

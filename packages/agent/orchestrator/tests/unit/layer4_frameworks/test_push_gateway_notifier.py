from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.orchestrator.turn_meter import (
    TurnMeter,
    reset_turn,
    start_turn,
)
from app.layer1_domain.entities.orchestrator.ui_event import UIEventType, UIPayloadType
from app.layer4_frameworks.messaging.push_gateway_notifier import (
    PersistingPushGatewayNotifier,
)


class RecordingLogger:
    def info(self, *args, **kwargs) -> None: ...
    def error(self, *args, **kwargs) -> None: ...


def _subject(agent_event_repo=None):
    inner = SimpleNamespace(send_notification=AsyncMock(), close=AsyncMock())
    repo = agent_event_repo or SimpleNamespace(
        save_event=MagicMock(), upsert_timeline_event=MagicMock()
    )
    uuid_values = iter(f"uuid-{i}" for i in range(1, 10))
    uuid_builder = SimpleNamespace(create_uuid7=lambda: next(uuid_values))
    logger = RecordingLogger()
    return (
        PersistingPushGatewayNotifier(
            notifier=inner,
            agent_event_repo=repo,
            uuid_builder=uuid_builder,
            logger=logger,
        ),
        inner,
        repo,
    )


@pytest.mark.asyncio
async def test_send_token_info_processing_pushes_as_chat_thinking_without_persisting():
    subject, inner, repo = _subject()

    await subject.send_token_info(
        conv_id="conv-1",
        message_id="msg-1",
        content="Cooking… 1.1s · 820 tokens",
        status="processing",
    )

    repo.save_event.assert_not_called()
    repo.upsert_timeline_event.assert_not_called()
    inner.send_notification.assert_awaited_once()
    pushed_event = inner.send_notification.await_args.kwargs["data"]
    assert pushed_event["event_type"] == UIEventType.CHAT_THINKING.value
    assert pushed_event["payload"]["type"] is None
    assert pushed_event["payload"]["message_id"] == "msg-1"
    assert pushed_event["payload"]["status"] == "processing"


@pytest.mark.asyncio
async def test_send_token_info_success_persists_and_pushes_as_chat_response_with_type():
    subject, inner, repo = _subject()

    await subject.send_token_info(
        conv_id="conv-1",
        message_id="msg-1",
        content="✻ Cooked for 1.1s, 820 tokens",
        status="success",
    )

    saved_event = repo.save_event.call_args.args[0]
    assert saved_event.event_type == UIEventType.CHAT_RESPONSE
    assert saved_event.payload.type == UIPayloadType.TOKEN_INFO
    assert saved_event.payload.message_id == "msg-1"
    assert saved_event.payload.status == "success"
    assert saved_event.event_id != "msg-1"
    inner.send_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_chat_response_with_active_turn_emits_answer_then_success_token_info_last():
    subject, inner, repo = _subject()

    meter = TurnMeter(message_id="msg-1", conv_id="conv-1", start_perf=0.0, total_tokens=1682)
    token = start_turn(meter)
    try:
        await subject.send_chat_response("conv-1", "final answer")
    finally:
        reset_turn(token)

    assert repo.save_event.call_count == 2
    content_event = repo.save_event.call_args_list[0].args[0]
    token_info_event = repo.save_event.call_args_list[1].args[0]

    assert content_event.event_id == "msg-1"
    assert content_event.payload.id == "msg-1"

    assert token_info_event.event_type == UIEventType.CHAT_RESPONSE
    assert token_info_event.payload.type == UIPayloadType.TOKEN_INFO
    assert token_info_event.payload.status == "success"
    assert token_info_event.payload.message_id == "msg-1"
    assert token_info_event.payload.content.startswith("✻ Cooked for ")


@pytest.mark.asyncio
async def test_send_chat_response_without_active_turn_skips_token_info():
    subject, inner, repo = _subject()

    await subject.send_chat_response("conv-1", "final answer")

    assert repo.save_event.call_count == 1
    content_event = repo.save_event.call_args_list[0].args[0]
    assert content_event.payload.id != "msg-1"

import asyncio
from types import SimpleNamespace

import pytest
from agent_sdk.layer1_domain.entities.agent_shared_state import TaskStatus
from agent_sdk.layer1_domain.entities.llm_usage import LLMUsageRecord

from app.layer1_domain.entities.turn_meter import TurnMeter, reset_turn, start_turn
from app.layer4_frameworks.messaging.push_gateway_usage_recorder import (
    FanOutLLMUsageRecorder,
    PushGatewayUsageRecorder,
    wire_llm_usage_recorder,
)


class RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_token_info_event(self, *, conv_id, message_id, content, status) -> None:
        self.calls.append(
            {
                "conv_id": conv_id,
                "message_id": message_id,
                "content": content,
                "status": status,
            }
        )


def _usage_record(total_tokens: int, conversation_id: str = "conv-fallback") -> LLMUsageRecord:
    return LLMUsageRecord(conversation_id=conversation_id, total_tokens=total_tokens)


async def _drain_pending_tasks() -> None:
    # scheduled emits run via loop.create_task(...); yield control so they execute.
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_record_without_active_turn_emits_nothing() -> None:
    notifier = RecordingNotifier()
    recorder = PushGatewayUsageRecorder(notifier=notifier)

    recorder.record(_usage_record(100))
    await _drain_pending_tasks()

    assert notifier.calls == []


@pytest.mark.asyncio
async def test_two_calls_in_one_turn_are_cumulative_processing_with_shared_message_id() -> None:
    notifier = RecordingNotifier()
    recorder = PushGatewayUsageRecorder(notifier=notifier)
    meter = TurnMeter.start_now(message_id="msg-1", conv_id="conv-1")
    token = start_turn(meter)
    try:
        recorder.record(_usage_record(820))
        recorder.record(_usage_record(862))
        await _drain_pending_tasks()
    finally:
        reset_turn(token)

    assert len(notifier.calls) == 2
    assert all(call["status"] == TaskStatus.PROCESSING for call in notifier.calls)
    assert all(call["message_id"] == "msg-1" for call in notifier.calls)
    assert all(call["conv_id"] == "conv-1" for call in notifier.calls)
    assert "820" in notifier.calls[0]["content"]
    assert "1,682" in notifier.calls[1]["content"]


@pytest.mark.asyncio
async def test_new_turn_gets_a_new_message_id() -> None:
    notifier = RecordingNotifier()
    recorder = PushGatewayUsageRecorder(notifier=notifier)

    first_meter = TurnMeter.start_now(message_id="msg-1", conv_id="conv-1")
    first_token = start_turn(first_meter)
    try:
        recorder.record(_usage_record(100))
        await _drain_pending_tasks()
    finally:
        reset_turn(first_token)

    second_meter = TurnMeter.start_now(message_id="msg-2", conv_id="conv-1")
    second_token = start_turn(second_meter)
    try:
        recorder.record(_usage_record(50))
        await _drain_pending_tasks()
    finally:
        reset_turn(second_token)

    assert [call["message_id"] for call in notifier.calls] == ["msg-1", "msg-2"]


@pytest.mark.asyncio
async def test_fan_out_recorder_calls_every_wrapped_recorder() -> None:
    class SpyRecorder:
        def __init__(self) -> None:
            self.records: list[LLMUsageRecord] = []

        def record(self, record: LLMUsageRecord) -> None:
            self.records.append(record)

    original = SpyRecorder()
    notifier = RecordingNotifier()
    push_gateway_recorder = PushGatewayUsageRecorder(notifier=notifier)
    fan_out = FanOutLLMUsageRecorder([original, push_gateway_recorder])
    meter = TurnMeter.start_now(message_id="msg-1", conv_id="conv-1")
    token = start_turn(meter)
    try:
        record = _usage_record(42)
        fan_out.record(record)
        await _drain_pending_tasks()
    finally:
        reset_turn(token)

    assert original.records == [record]
    assert len(notifier.calls) == 1


def test_wire_llm_usage_recorder_composes_fanout_with_existing_recorder() -> None:
    class ExistingRecorder:
        def record(self, record: LLMUsageRecord) -> None: ...

    existing = ExistingRecorder()
    llm = SimpleNamespace(usage_recorder=existing, _chat_client=object())
    notifier = RecordingNotifier()

    wire_llm_usage_recorder(llm, notifier)

    assert isinstance(llm.usage_recorder, FanOutLLMUsageRecorder)
    assert llm.usage_recorder._recorders[0] is existing
    assert isinstance(llm.usage_recorder._recorders[1], PushGatewayUsageRecorder)


def test_wire_llm_usage_recorder_without_existing_recorder() -> None:
    llm = SimpleNamespace(usage_recorder=None, _chat_client=None)
    notifier = RecordingNotifier()

    wire_llm_usage_recorder(llm, notifier)

    assert isinstance(llm.usage_recorder, FanOutLLMUsageRecorder)
    assert len(llm.usage_recorder._recorders) == 1


def test_wire_llm_usage_recorder_drops_already_cached_chat_client() -> None:
    # get_chat_client() bakes `usage_recorder` into the client on its first call and caches
    # it forever; build_app_container() already triggers that call before this wiring runs,
    # so the cache must be dropped or the fan-out recorder would never be used.
    llm = SimpleNamespace(usage_recorder=None, _chat_client="stale-cached-client")
    notifier = RecordingNotifier()

    wire_llm_usage_recorder(llm, notifier)

    assert llm._chat_client is None


def test_wire_llm_usage_recorder_tolerates_missing_chat_client_attribute() -> None:
    llm = SimpleNamespace(usage_recorder=None)
    notifier = RecordingNotifier()

    wire_llm_usage_recorder(llm, notifier)

    assert not hasattr(llm, "_chat_client")

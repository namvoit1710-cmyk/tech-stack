"""SA-2079: bridges every SDK `LLMUsageRecord` to a `token_info` push-gateway event for the
active turn (the TurnMeter contextvar set at task-run entry). Zero agent-sdk diff, zero
call-site changes — this only wraps `ILLMUsageRecorder`, the SDK's existing usage-tracking
seam (`UsageTrackingChatModel._record_success()` -> `ILLMUsageRecorder.record()`)."""
import asyncio
from typing import Any, Sequence

from agent_sdk.layer1_domain.entities.agent_shared_state import TaskStatus
from agent_sdk.layer1_domain.entities.llm_usage import LLMUsageRecord
from agent_sdk.layer2_application.interfaces.llm_usage_recorder import ILLMUsageRecorder

from app.layer1_domain.entities.turn_meter import get_turn_meter
from app.layer2_application.interfaces.push_gateway_notifier_interface import (
    IPushGatewayNotifier,
)


class PushGatewayUsageRecorder(ILLMUsageRecorder):
    def __init__(self, notifier: IPushGatewayNotifier) -> None:
        self._notifier = notifier

    def record(self, record: LLMUsageRecord) -> None:
        meter = get_turn_meter()
        if meter is None:
            return
        meter.record_call(record.total_tokens)
        content = meter.render(done=False)
        conv_id = meter.conv_id or record.conversation_id
        self._schedule_emit(conv_id=conv_id, message_id=meter.message_id, content=content)

    def _schedule_emit(self, *, conv_id: str, message_id: str, content: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            self._notifier.send_token_info_event(
                conv_id=conv_id,
                message_id=message_id,
                content=content,
                status=TaskStatus.PROCESSING,
            )
        )


class FanOutLLMUsageRecorder(ILLMUsageRecorder):
    """Delegates each usage record to every wrapped recorder, in order."""

    def __init__(self, recorders: Sequence[ILLMUsageRecorder]) -> None:
        self._recorders: tuple[ILLMUsageRecorder, ...] = tuple(recorders)

    def record(self, record: LLMUsageRecord) -> None:
        for recorder in self._recorders:
            recorder.record(record)


def wire_llm_usage_recorder(llm: Any, notifier: IPushGatewayNotifier) -> None:
    """Compose `llm.usage_recorder` into a fan-out of the existing recorder (if any) plus a
    new `PushGatewayUsageRecorder`, and drop any already-cached LangChain chat client so the
    new recorder actually reaches real LLM calls.

    `LangChainLLMService.get_chat_client()` bakes whichever `usage_recorder` is set at the
    time of its FIRST call into the returned client and caches that client forever;
    `agent_sdk.bootstrap.build_app_container()` already triggers that first call (populating
    `deps["llm"]`) before this composition root runs, so without dropping the cache this
    reassignment would silently never take effect.
    """
    existing_usage_recorder = getattr(llm, "usage_recorder", None)
    push_gateway_usage_recorder = PushGatewayUsageRecorder(notifier=notifier)
    llm.usage_recorder = FanOutLLMUsageRecorder(
        [r for r in (existing_usage_recorder, push_gateway_usage_recorder) if r is not None]
    )
    if getattr(llm, "_chat_client", None) is not None:
        llm._chat_client = None

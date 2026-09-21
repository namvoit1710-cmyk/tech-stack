"""SA-2079: wraps `ILLMProvider`, timing each call and emitting a `token_info` push-gateway
event for the active turn (the TurnMeter contextvar set at turn entry). No additional LLM
round-trip: usage is read off the wrapped provider's own completion via `usage_observer`."""
import asyncio
from typing import Any, Type

from app.layer1_domain.entities.orchestrator.turn_meter import get_turn_meter
from app.layer1_domain.interfaces.llm_provider_interface import ILLMProvider
from app.layer2_application.interfaces.push_gateway_notifier import IPushGatewayNotifier


class InstrumentedLLMProvider:
    def __init__(self, inner: ILLMProvider, notifier: IPushGatewayNotifier) -> None:
        self._inner = inner
        self._notifier = notifier
        self._last_usage: Any = None
        if hasattr(inner, "usage_observer"):
            inner.usage_observer = self._capture_usage

    def _capture_usage(self, usage: Any) -> None:
        self._last_usage = usage

    def complete_with_format(
        self, *, messages: list[dict[str, str]], response_model: Type[Any]
    ) -> Any:
        self._last_usage = None
        result = self._inner.complete_with_format(
            messages=messages, response_model=response_model
        )
        meter = get_turn_meter()
        if meter is not None:
            total_tokens = getattr(self._last_usage, "total_tokens", None)
            meter.record_call(total_tokens)
            self._schedule_emit(
                conv_id=meter.conv_id,
                message_id=meter.message_id,
                content=meter.render(done=False),
            )
        return result

    def _schedule_emit(self, *, conv_id: str, message_id: str, content: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            self._notifier.send_token_info(
                conv_id=conv_id, message_id=message_id, content=content, status="processing"
            )
        )

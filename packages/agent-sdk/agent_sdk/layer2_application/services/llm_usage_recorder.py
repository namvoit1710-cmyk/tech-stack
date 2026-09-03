from __future__ import annotations

from agent_sdk.layer1_domain.entities.llm_usage import LLMUsageRecord
from agent_sdk.layer2_application.interfaces.llm_usage_recorder import ILLMUsageRecorder
from agent_sdk.layer2_application.interfaces.observability import ILogger, IMonitor


def _format_cost_amount_for_log(value: float, *, decimal_places: int = 8) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = 0.0
    places = max(int(decimal_places), 0)
    return f"{amount:.{places}f}"


def _format_cost_for_log(
    value: float,
    currency: str,
    *,
    decimal_places: int = 8,
) -> str:
    currency_value = (currency or "USD").strip() or "USD"
    return (
        f"{_format_cost_amount_for_log(value, decimal_places=decimal_places)} "
        f"{currency_value}"
    )


def _record_to_log_dict(record: LLMUsageRecord) -> dict[str, object]:
    data = record.to_dict()
    currency = str(data.get("currency") or "USD")
    data["input_cost"] = _format_cost_for_log(record.input_cost, currency)
    data["output_cost"] = _format_cost_for_log(record.output_cost, currency)
    data["total_cost"] = _format_cost_for_log(record.total_cost, currency)
    return data


class LoggingLLMUsageRecorder(ILLMUsageRecorder):
    def __init__(
        self,
        *,
        logger: ILogger,
        monitor: IMonitor | None = None,
        enabled: bool = True,
    ) -> None:
        self._logger = logger
        self._monitor = monitor
        self._enabled = enabled

    def record(self, record: LLMUsageRecord) -> None:
        if not self._enabled:
            return

        data = _record_to_log_dict(record)
        event = str(data.pop("event", "llm.usage"))
        self._logger.info(event, **data)

        if self._monitor is None:
            return
        self._monitor.track("llm_usage_requests", 1)
        self._monitor.track("llm_usage_total_tokens", float(record.total_tokens))
        if record.total_cost:
            self._monitor.track("llm_usage_total_cost", float(record.total_cost))
        if not record.success:
            self._monitor.track("llm_usage_errors", 1)

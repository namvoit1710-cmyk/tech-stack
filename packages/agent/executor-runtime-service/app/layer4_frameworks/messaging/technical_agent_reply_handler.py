from __future__ import annotations

from typing import Any

from app.layer2_application.interfaces.observability import ILogger
from app.layer4_frameworks.services.agent_reply_waiter import AgentReplyWaiter


class TechnicalAgentReplyHandler:
    def __init__(self, waiter: AgentReplyWaiter, logger: ILogger) -> None:
        self._waiter = waiter
        self._logger = logger

    async def __call__(
        self,
        payload: dict[str, Any],
        _message_headers: dict[str, str] | None = None,
        _queue_name: str | None = None,
    ) -> None:
        if not isinstance(payload, dict):
            return

        correlation_id = str(payload.get("correlation_id") or "")
        if not correlation_id:
            self._logger.warning("Ignored technical agent reply without correlation_id")
            return

        status = self._extract_status(payload)
        if status in {"processing", "running", "in_progress", "interrupted"}:
            self._logger.debug(
                "Ignored non-terminal technical agent reply",
                correlation_id=correlation_id,
                status=status,
            )
            return

        self._logger.info(
            "Received terminal technical agent reply",
            correlation_id=correlation_id,
            status=status,
            reply_step=self._extract_step_payload(payload),
            raw_reply=dict(payload),
        )

        if not self._waiter.resolve(correlation_id, payload):
            self._logger.warning(
                "Ignored technical agent reply for unknown correlation_id",
                correlation_id=correlation_id,
            )

    def _extract_status(self, payload: dict[str, Any]) -> str:
        nested_payload = payload.get("payload") or {}
        if not isinstance(nested_payload, dict):
            nested_payload = {}
        step = nested_payload.get("step") or {}
        if not isinstance(step, dict):
            step = {}
        return str(step.get("status") or payload.get("status") or "").lower()

    def _extract_step_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        nested_payload = payload.get("payload") or {}
        if not isinstance(nested_payload, dict):
            return {}

        step = nested_payload.get("step") or {}
        if not isinstance(step, dict):
            return {}
        return dict(step)

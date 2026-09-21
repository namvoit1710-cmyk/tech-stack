"""SA-892 stop target: cancel the agent turn via ``agent.control`` (phase 1).

Publishes one ``agent.control`` cancel keyed by conv_id to every agent-sdk consumer
that could still be running/resuming a turn. Best-effort PER topic: a failure on one
target is recorded, never aborts the others.

B2 (config-driven fan-out): the target topics come from ``settings.AGENT_CONTROL_TOPICS``
(a comma-separated string OR a list) — adding a runtime is a config change, not a
code change. When that setting is absent it falls back to the historical
(business-agent, executor-runtime) pair, so existing behaviour is preserved verbatim.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_sdk.layer1_domain.value_objects.agent_control import (
    AGENT_CANCEL_ACTION,
    AGENT_CONTROL_MESSAGE_TYPE,
)
from agent_sdk.layer2_application.interfaces.message_publisher import IMessagePublisher
from app.layer2_application.interfaces.stop_target import (
    StopContext,
    StopTargetResult,
)

# The executor-runtime-service is also an agent-sdk consumer and handles control
# messages inline; a stop must reach it as well as the business agent.
DEFAULT_AGENT_EXECUTOR_CONTROL_TOPIC = "agent.request.executor"


class AgentControlStopTarget:
    """Fan an ``agent.control`` cancel out to the configured agent-sdk consumers."""

    name = "agent_control"
    phase = 1

    def __init__(
        self, publisher: IMessagePublisher, settings: Any, logger: Any
    ) -> None:
        self._publisher = publisher
        self._settings = settings
        self._logger = logger

    async def stop(self, ctx: StopContext) -> StopTargetResult:
        result = StopTargetResult(target=self.name)
        conv_id = ctx.conv_id
        control_message = {
            "type": AGENT_CONTROL_MESSAGE_TYPE,
            "action": AGENT_CANCEL_ACTION,
            "conv_id": conv_id,
            "correlation_id": conv_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        published: set[str] = set()
        for topic in self._topics():
            if not topic or topic in published:
                continue  # skip empty + dedupe (business == executor edge case)
            published.add(topic)
            try:
                await self._publisher.publish(
                    topic, message=control_message, key=conv_id
                )
            except Exception as exc:  # per-topic best-effort; never abort the other
                self._logger.warning(
                    "Agent cancel publish failed",
                    conv_id=conv_id,
                    topic=topic,
                    error=str(exc),
                )
                result.errors.append(f"agent cancel failed [{topic}]: {exc}")
                continue
            result.agent_cancelled = True
        return result

    def _topics(self) -> list[str]:
        """The agent-control fan-out topics.

        B2: an explicit ``AGENT_CONTROL_TOPICS`` list (comma-separated string or an
        iterable) wins. Otherwise fall back to the historical pair:
        ``AGENT_CONTROL_TOPIC``/``AGENT_REQUEST_TOPIC`` (business agent — its request
        topic doubles as its control topic) + the executor-runtime control topic.
        """
        raw = getattr(self._settings, "AGENT_CONTROL_TOPICS", None)
        if raw:
            if isinstance(raw, str):
                topics = [t.strip() for t in raw.split(",") if t.strip()]
            else:
                topics = [str(t).strip() for t in raw if str(t).strip()]
            if topics:
                return topics

        business_topic = getattr(
            self._settings, "AGENT_CONTROL_TOPIC", ""
        ) or getattr(self._settings, "AGENT_REQUEST_TOPIC", "")
        executor_topic = (
            getattr(self._settings, "AGENT_EXECUTOR_CONTROL_TOPIC", "")
            or getattr(self._settings, "AGENT_EXECUTOR_REQUEST_TOPIC", "")
            or DEFAULT_AGENT_EXECUTOR_CONTROL_TOPIC
        )
        return [business_topic, executor_topic]

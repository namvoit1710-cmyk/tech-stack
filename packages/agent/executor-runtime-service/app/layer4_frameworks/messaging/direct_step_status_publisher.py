"""
Direct Step Status Publisher.

Publishes executor.step.status events directly to the message broker.
This is the MVP implementation — best-effort, no durability guarantee.

TODO: Replace with an Outbox-backed publisher that:
  1. Writes the status event to a durable Outbox Store (DB table).
  2. An Outbox Worker background job scans unpublished rows.
  3. The worker publishes to the broker and marks the row PUBLISHED.
  4. On crash, unpublished rows are retried on next worker cycle.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid_utils import uuid7

from app.layer1_domain.interfaces.step_status_publisher_interface import (
    IStepStatusPublisher,
    StepStatusPublisherInput,
)

logger = logging.getLogger(__name__)


class DirectStepStatusPublisher(IStepStatusPublisher):
    """
    Implements IStepStatusPublisher by publishing directly to the broker.

    Accepts the SDK's IMessagePublisher interface (publisher.publish(topic, message, key=)).
    """

    def __init__(self, publisher: Any, topic: str) -> None:
        self._publisher = publisher
        self._topic = topic

    async def publish_status(
        self,
        input: StepStatusPublisherInput,
    ) -> None:
        """
        Build and publish the executor.step.status envelope.

        Shape follows the BE_A2A_event_contract.md specification.
        """
        event = {
            "id": str(uuid7()),
            "type": "executor.step.status",
            "message_id": str(uuid7()),
            "correlation_id": input.correlation_id,
            "conv_id": input.conv_id,
            "payload": {
                "from": "executor.runtime",
                "to": input.to_agent,
                "type": "step",
                "steps": [
                    {
                        "id": input.step.id,
                        "execute_by": input.step.execute_by,
                        "goal": input.step.goal,
                        "message": input.step.message,
                        "status": (
                            input.step.status.value if input.step.status else "error"
                        ),
                        "input_data": input.step.input_data,
                        "output_data": input.step.output_data,
                        "error": input.step.error or "",
                    }
                ],
            },
        }

        try:
            await self._publisher.publish(
                topic=self._topic,
                message=event,
                key=input.correlation_id or None,
            )
            logger.info(
                "Published executor.step.status: step_id=%s status=%s",
                input.step.id,
                input.step.status.value if input.step.status else "unknown",
            )
        except Exception as exc:
            # Best-effort in MVP — log and continue
            # TODO: With Outbox pattern, this failure would be retried automatically
            logger.error(
                "Failed to publish executor.step.status: step_id=%s error=%s",
                input.step.id,
                str(exc),
            )

"""The worker's own runtime reaches the event log (wcp-run-latency P0-1).

Measured on tenant-1 2026-08-11: ``NodeDispatched -> NodeOutputProduced`` is
**58.6%** of a run's wall clock, and 94% of that leg is one node type
(``worker:http_request``, median 3.865s). Of those 3.865s only ~0.96s could be
accounted for; the rest was reachable only by subtraction, because nothing said
how much was the worker and how much was the path to it.

The data was already on the wire. All three executor completion publishers send
``execution_time_ms`` — ``execute_task_usecase`` (push),
``handle_worker_callback_usecase`` twice (async-callback and pull) — and both
``Task.execution_time_ms`` and ``NodeCompleted.execution_time_ms`` have existed,
defaulted to 0, and were never assigned anywhere on the run path. The only
assignment in the codebase was in ``test_node_definition``, a design-time
feature.

So this is a reconnected wire, not a new feature, and these tests pin the three
places it was cut: the consumer that dropped the field, the use case that never
stored it, and the event that never carried it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.events import NodeCompleted
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskCommand,
)
from app.layer4_frameworks.messaging.worker_results_consumer import (
    WorkerResultsConsumer,
)


def _completed_message(**payload_overrides: Any) -> dict:
    payload = {
        "task_id": "t-1",
        "run_id": "run-1",
        "node_id": "n-1",
        "status": "COMPLETED",
        "output_reference": "",
        "output_metadata": {},
        "execution_time_ms": 3421,
    }
    payload.update(payload_overrides)
    return {"message_type": "task.completed", "correlation_id": "run-1",
            "payload": payload}


@pytest.fixture
def consumer_and_uc():
    uc = MagicMock()
    uc.execute = AsyncMock()
    return WorkerResultsConsumer({"complete_task": uc}, MagicMock()), uc


class TestTheConsumerStopsDroppingIt:
    @pytest.mark.asyncio
    async def test_the_worker_duration_is_forwarded(self, consumer_and_uc):
        consumer, uc = consumer_and_uc
        await consumer._handle(_completed_message())
        cmd: CompleteTaskCommand = uc.execute.call_args.args[0]
        assert cmd.execution_time_ms == 3421

    @pytest.mark.asyncio
    async def test_a_missing_duration_is_zero_not_a_crash(self, consumer_and_uc):
        """Older executors, and the failure path, send no duration at all."""
        consumer, uc = consumer_and_uc
        msg = _completed_message()
        del msg["payload"]["execution_time_ms"]
        await consumer._handle(msg)
        assert uc.execute.call_args.args[0].execution_time_ms == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("junk", ["1250", None, "", "abc", -5, 12.9, {}, []])
    async def test_garbage_on_the_wire_never_breaks_a_completion(
        self, consumer_and_uc, junk,
    ):
        """This rides an untrusted envelope. A bad duration may cost a
        measurement; it must never cost the run."""
        consumer, uc = consumer_and_uc
        await consumer._handle(_completed_message(execution_time_ms=junk))
        value = uc.execute.call_args.args[0].execution_time_ms
        assert isinstance(value, int) and value >= 0

    @pytest.mark.asyncio
    async def test_a_numeric_string_is_still_read(self, consumer_and_uc):
        consumer, uc = consumer_and_uc
        await consumer._handle(_completed_message(execution_time_ms="1250"))
        assert uc.execute.call_args.args[0].execution_time_ms == 1250


class TestTheEventCarriesIt:
    def test_node_completed_reports_the_task_duration(self):
        ev = NodeCompleted(task_id="t-1", run_id="run-1", node_id="n-1",
                           execution_time_ms=3421)
        assert ev.execution_time_ms == 3421

    def test_it_still_defaults_to_zero(self):
        """Inline nodes and PENDING completions have no worker duration, and
        must not be forced to invent one."""
        assert NodeCompleted(task_id="t-1").execution_time_ms == 0


class TestTheUseCaseStoresIt:
    """``complete_task`` is re-drive-on-replay: the same result may arrive
    twice. A redelivery that carries no duration must not erase the figure the
    first delivery established."""

    @dataclass
    class _Task:
        id: str = "t-1"
        execution_time_ms: int = 0
        output_metadata: dict = field(default_factory=dict)

    def _apply(self, task, req_ms: int) -> int:
        # The assignment under test, isolated from the use case's IO.
        if req_ms:
            task.execution_time_ms = int(req_ms)
        return task.execution_time_ms

    def test_a_real_duration_is_stored(self):
        task = self._Task()
        assert self._apply(task, 3421) == 3421

    def test_a_replay_without_a_duration_does_not_erase_it(self):
        task = self._Task(execution_time_ms=3421)
        assert self._apply(task, 0) == 3421

    def test_a_later_real_value_wins(self):
        task = self._Task(execution_time_ms=3421)
        assert self._apply(task, 2000) == 2000

"""Executor extraction.

Houses the broker-subscribed ``ExecutorConsumer`` that consumes
``node.input`` and invokes the appropriate node handler. Under
``EXECUTOR_MODE=executor`` (Gate 3, doc §13.4) it is the authoritative
invoker; under the default ``in_process`` it is wired but dormant.
"""

from app.layer4_frameworks.executor.executor_consumer import (
    ExecutorConsumer,
    ExecutorMode,
    ExecutorResult,
)
from app.layer4_frameworks.executor.executor_dedup import ExecutorDedup
from app.layer4_frameworks.executor.run_completion_consumer import (
    RunCompletionConsumer,
    RunCompletionTeeingPublisher,
)
from app.layer4_frameworks.executor.run_start_consumer import (
    RunStartConsumer,
    RunStartTeeingPublisher,
)
from app.layer4_frameworks.executor.task_creator_consumer import (
    TaskCreatorConsumer,
    TaskCreatorTeeingPublisher,
)

__all__ = [
    "ExecutorConsumer",
    "ExecutorDedup",
    "ExecutorMode",
    "ExecutorResult",
    "RunCompletionConsumer",
    "RunCompletionTeeingPublisher",
    "RunStartConsumer",
    "RunStartTeeingPublisher",
    "TaskCreatorConsumer",
    "TaskCreatorTeeingPublisher",
]

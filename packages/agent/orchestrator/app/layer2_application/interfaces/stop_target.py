"""SA-892 stop fan-out — port for a single stop target (Ports & Adapters).

A Stop fans out to several downstream targets (cancel the current message's tasks,
cancel the agent turn, stop workflow runs). Each is a ``StopTarget`` adapter behind
this port, so ``StopConversationUseCase`` iterates a registered list instead of
hardcoding the set — adding a target means registering an adapter, not editing the
use case.

layer2 contract: base Python only — ``typing.Protocol`` + ``@dataclass``; no ABC, no
framework, no Pydantic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class StopContext:
    """Everything a target needs to stop, keyed by conversation.

    Deliberately thin: collaborators (publisher, repo, registry) are injected into
    each adapter at construction; the context carries only the identity of what to
    stop. It can grow (e.g. a cached shared-state read) without touching adapters
    that do not use the new field.
    """

    conv_id: str


@dataclass
class StopTargetResult:
    """What a single target did — merged into ``StopConversationOutput``.

    Every field is additive: the use case ORs ``agent_cancelled`` and extends the
    list fields across all targets, preserving the existing FE contract.
    """

    target: str
    tasks_cancelled: list[str] = field(default_factory=list)
    runs_cancelled: list[str] = field(default_factory=list)
    agent_cancelled: bool = False
    errors: list[str] = field(default_factory=list)


class StopTarget(Protocol):
    """A single destination a Stop fans out to.

    ``phase`` orders execution: every lower-phase target completes before any
    higher-phase target starts (SA-892 invariant — task-cancel at phase 0 must
    precede the external fan-out so a late result is dropped by the business-agent's
    per-task guard). ``stop`` is best-effort: it must not raise for an expected
    failure — it records the failure in ``StopTargetResult.errors`` so one target
    never aborts another.
    """

    name: str
    phase: int

    async def stop(self, ctx: StopContext) -> StopTargetResult: ...

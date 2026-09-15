"""A start that cannot succeed must not be retried forever.

``EventTriggerConsumer`` treats every exception as retryable: an exception
means no ack, which means the broker sends the event again. That is correct
for a HANA blip or a network fault. It is wrong for a verdict.

Measured on tenant-1 (SA-2280): a Jira-triggered workflow hit a vault 403 on a
user-owned credential — event-triggered runs have no user, and the owner check
is user-scoped (SA-2279). The 403 is deterministic, so every redelivery hit it
again. Nothing dead-lettered the event and no run existed to carry the error,
so the workflow was simply silent while the retry loop drove the event-loop lag
peak to 719 ms.

The distinction these tests defend is narrow on purpose: only *known*
deterministic failures ack. Misclassifying a transient fault as terminal loses
the event, which is worse than retrying it.
"""

from __future__ import annotations

import pytest

from app.layer4_frameworks.clients.http_vault_credential_client import (
    CredentialNotFound,
    CredentialResolveAuthError,
)
from tests.unit.test_event_trigger_consumer import (  # reuse the real harness
    _envelope,
    _event_workflow,
    _make,
    _Monitor,
)
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
    MemoryWorkflowRepository,
)

TOPIC = "t.jira"


class _ExplodingStartRun:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls = 0

    async def execute(self, command):
        self.calls += 1
        raise self._exc


def _repo() -> MemoryWorkflowRepository:
    repo = MemoryWorkflowRepository()
    repo.save(_event_workflow("wf-jira", TOPIC))
    return repo


class TestTerminalFailuresAreAcked:
    @pytest.mark.parametrize("exc", [
        CredentialResolveAuthError("vault rejected resolve of 'cred-1': 403"),
        CredentialNotFound("cred-1"),
    ])
    @pytest.mark.asyncio
    async def test_the_event_is_not_redelivered(self, exc):
        """No raise out of ``_handle`` == the broker acks == the loop stops."""
        monitor = _Monitor()
        consumer, _ = _make(repo=_repo(), start_run=_ExplodingStartRun(exc),
                            monitor=monitor)

        await consumer._handle(_envelope(topic=TOPIC))   # must not raise

        assert ("event_trigger_message", {"outcome": "start_refused",
                                          "event_topic": TOPIC}) in monitor.calls

    @pytest.mark.asyncio
    async def test_it_is_loud_about_it(self, caplog):
        """Acking a failure silently would trade an infinite retry for an
        invisible drop — strictly worse, because at least the loop was
        noticeable."""
        consumer, _ = _make(
            repo=_repo(),
            start_run=_ExplodingStartRun(
                CredentialResolveAuthError(
                    "vault rejected resolve of 'cred-abc': 403")),
        )
        with caplog.at_level("ERROR"):
            await consumer._handle(_envelope(topic=TOPIC, event_id="evt-9"))

        text = caplog.text
        assert "cred-abc" in text, "the operator cannot act without the id"
        assert "wf-jira" in text
        assert "evt-9" in text


class TestTransientFailuresStillRedeliver:
    @pytest.mark.asyncio
    async def test_an_unclassified_error_still_raises(self):
        """The safety property: anything not known-deterministic keeps the old
        at-least-once behaviour. Losing an event to a momentary DB outage is
        the failure mode this guards against."""
        consumer, _ = _make(repo=_repo(),
                            start_run=_ExplodingStartRun(
                                RuntimeError("HANA connection reset")))
        with pytest.raises(Exception):
            await consumer._handle(_envelope(topic=TOPIC))

    @pytest.mark.asyncio
    async def test_a_rejected_result_still_raises(self):
        """``result.error`` (as opposed to a raised exception) is the existing
        retry path and is deliberately untouched."""
        class _Rejecting:
            async def execute(self, command):
                from types import SimpleNamespace
                return SimpleNamespace(error="workflow is not published", id="")

        consumer, _ = _make(repo=_repo(), start_run=_Rejecting())
        with pytest.raises(Exception):
            await consumer._handle(_envelope(topic=TOPIC))


class TestOneBadWorkflowDoesNotBlockTheOthers:
    @pytest.mark.asyncio
    async def test_a_refused_start_does_not_stop_the_fan_out(self):
        """1→N routing: a credential problem in one workflow must not stop
        every other subscriber to the same topic from running."""
        repo = MemoryWorkflowRepository()
        repo.save(_event_workflow("wf-broken", TOPIC))
        repo.save(_event_workflow("wf-healthy", TOPIC))

        started: list[str] = []

        class _Selective:
            async def execute(self, command):
                if command.workflow_id == "wf-broken":
                    raise CredentialResolveAuthError(
                        "vault rejected resolve of 'cred-1': 403")
                started.append(command.workflow_id)
                from types import SimpleNamespace
                return SimpleNamespace(error=None, id="run-1")

        consumer, _ = _make(repo=repo, start_run=_Selective())
        await consumer._handle(_envelope(topic=TOPIC))

        assert started == ["wf-healthy"], (
            "the healthy workflow was skipped because a sibling's credential "
            "was refused"
        )

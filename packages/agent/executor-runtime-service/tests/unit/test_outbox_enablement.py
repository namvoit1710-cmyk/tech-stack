"""
Verification: the executor is switched onto the agent-sdk transactional outbox
when HANA is configured.

We do NOT build an outbox in this service — the SDK ships it. These tests prove the
*enablement*: with HANA_* set, the executor's bootstrap yields a publisher wrapped by
the SDK's OutboxPublisher (persist-then-publish), and the executor's status publisher
publishes THROUGH it — so executor.step.status events become durable. With HANA unset,
the publisher stays the plain broker publisher (today's fire-and-forget behavior).

HANA repos are mocked (same pattern as the SDK's own test_inbox_outbox_wiring), so this
is deterministic and needs no live database.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

SDK = "agent_sdk"

_HANA_TARGETS = (
    "layer4_frameworks.persistence.hana.hana_connection_manager.HanaConnectionManager",
    "layer4_frameworks.persistence.hana.hana_shared_state_repository.HanaSharedStateRepository",
    "layer4_frameworks.persistence.hana.hana_agent_shared_state_repository.HanaAgentSharedStateRepository",
    "layer4_frameworks.persistence.hana.hana_inbox_repository.HanaInboxRepository",
    "layer4_frameworks.persistence.hana.hana_outbox_repository.HanaOutboxRepository",
)


def _build_with_hana(monkeypatch):
    monkeypatch.setenv("HANA_HOST", "localhost")
    monkeypatch.setenv("HANA_PORT", "39041")
    monkeypatch.setenv("HANA_USERNAME", "SYSTEM")
    monkeypatch.setenv("HANA_PASSWORD", "test-pw")
    patches = [patch(f"{SDK}.{t}", return_value=MagicMock()) for t in _HANA_TARGETS]
    for p in patches:
        p.start()
    try:
        bootstrap = importlib.import_module("bootstrap")
        return bootstrap.build_app_container()
    finally:
        for p in patches:
            p.stop()


def test_outbox_enabled_when_hana_configured(executor_env):
    container = _build_with_hana(executor_env)

    from agent_sdk.layer2_application.services.outbox_publisher import OutboxPublisher

    # The SDK wrapped the broker publisher with the durable OutboxPublisher.
    assert isinstance(container["publisher"], OutboxPublisher)
    # An outbox repository was wired.
    assert container["_dependencies"].get("outbox_repository") is not None
    # The executor's status publisher publishes THROUGH the outbox publisher,
    # so executor.step.status is now durable without any change to the handler.
    status_publisher = container["status_publisher"]
    assert status_publisher._publisher is container["publisher"]


def test_outbox_disabled_without_hana(bootstrap_module):
    # Base executor_env sets no HANA_HOST → SDK keeps the plain broker publisher.
    container = bootstrap_module.build_app_container()

    from agent_sdk.layer2_application.services.outbox_publisher import OutboxPublisher

    assert not isinstance(container["publisher"], OutboxPublisher)
    assert container["_dependencies"].get("outbox_repository") is None

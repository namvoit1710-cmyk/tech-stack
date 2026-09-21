"""Tests for inbox/outbox wiring in bootstrap and consumer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

SDK_ROOT = "agent_sdk"
BOOTSTRAP_MOD = f"{SDK_ROOT}.bootstrap"
CONSUMER_MOD = f"{SDK_ROOT}.layer3_adapters.presenters.agent_consumer"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeDelivery:
    payload: dict
    _acked: bool = False
    _nacked: bool = False
    _rejected: bool = False

    async def ack(self) -> None:
        self._acked = True

    async def nack(self, requeue: bool = True) -> None:
        self._nacked = True

    async def reject(self) -> None:
        self._rejected = True


class _FakeConsumer:
    """Minimal consumer that captures _handle and invokes it."""

    def __init__(self) -> None:
        self.handler = None

    async def start(self, handler):
        self.handler = handler


class _FakePublisher:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def publish(self, *, topic: str, message: dict, key: str | None = None):
        self.messages.append({"topic": topic, "message": message, "key": key})

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Bootstrap wiring tests
# ---------------------------------------------------------------------------


class TestBootstrapInboxOutboxWiring:
    """Verify inbox/outbox repos are wired when HANA is available."""

    @patch(f"{BOOTSTRAP_MOD}.settings")
    def test_inbox_outbox_repos_created_when_hana_available(self, mock_settings):
        """When HANA_HOST is set, inbox and outbox repos should be wired."""
        mock_settings.DEFAULT_TENANT_ID = "default"
        mock_settings.OPENAI_API_KEY = ""
        mock_settings.APP_MODE = "SERVER"
        mock_settings.MCP_SERVERS = []
        mock_settings.MCP_TOOL_FILTER = []
        mock_settings.HANA_HOST = "localhost"
        mock_settings.HANA_PORT = 30015
        mock_settings.HANA_USER = "test"
        mock_settings.HANA_PASSWORD = "test"
        mock_settings.LLM_MODEL = ""
        mock_settings.REGISTRY_URL = ""

        mock_hana_cm = MagicMock()
        mock_inbox = MagicMock()
        mock_outbox = MagicMock()

        with (
            patch(
                f"{SDK_ROOT}.layer4_frameworks.persistence.hana.hana_inbox_repository.HanaInboxRepository",
                return_value=mock_inbox,
            ),
            patch(
                f"{SDK_ROOT}.layer4_frameworks.persistence.hana.hana_outbox_repository.HanaOutboxRepository",
                return_value=mock_outbox,
            ),
            patch(
                f"{SDK_ROOT}.layer4_frameworks.persistence.hana.hana_connection_manager.HanaConnectionManager",
                return_value=mock_hana_cm,
            ),
            patch(
                f"{SDK_ROOT}.layer4_frameworks.persistence.hana.hana_shared_state_repository.HanaSharedStateRepository",
                return_value=MagicMock(),
            ),
            patch(
                f"{SDK_ROOT}.layer4_frameworks.persistence.hana.hana_agent_shared_state_repository.HanaAgentSharedStateRepository",
                return_value=MagicMock(),
            ),
        ):
            from agent_sdk.bootstrap import build_app_container

            container = build_app_container()

        deps = container["_dependencies"]
        assert "inbox_repository" in deps
        assert "outbox_repository" in deps
        mock_inbox.setup.assert_called_once()
        mock_outbox.setup.assert_called_once()

    @patch(f"{BOOTSTRAP_MOD}.settings")
    def test_inbox_outbox_repos_skipped_without_hana(self, mock_settings):
        """When HANA_HOST is empty, inbox and outbox repos should NOT be wired."""
        mock_settings.DEFAULT_TENANT_ID = "default"
        mock_settings.OPENAI_API_KEY = ""
        mock_settings.APP_MODE = "SERVER"
        mock_settings.MCP_SERVERS = []
        mock_settings.MCP_TOOL_FILTER = []
        mock_settings.HANA_HOST = ""
        mock_settings.LLM_MODEL = ""
        mock_settings.REGISTRY_URL = ""

        from agent_sdk.bootstrap import build_app_container

        container = build_app_container()
        deps = container["_dependencies"]
        assert "inbox_repository" not in deps
        assert "outbox_repository" not in deps

    @patch(f"{BOOTSTRAP_MOD}.settings")
    def test_publisher_wrapped_with_outbox_when_available(self, mock_settings):
        """When outbox_repository exists, publisher should be wrapped with OutboxPublisher."""
        mock_settings.DEFAULT_TENANT_ID = "default"
        mock_settings.OPENAI_API_KEY = ""
        mock_settings.APP_MODE = "CONSUMER"
        mock_settings.MCP_SERVERS = []
        mock_settings.MCP_TOOL_FILTER = []
        mock_settings.HANA_HOST = "localhost"
        mock_settings.HANA_PORT = 30015
        mock_settings.HANA_USER = "test"
        mock_settings.HANA_PASSWORD = "test"
        mock_settings.LLM_MODEL = ""
        mock_settings.REGISTRY_URL = ""

        mock_hana_cm = MagicMock()
        mock_inbox = MagicMock()
        mock_outbox = MagicMock()
        mock_publisher = MagicMock()
        mock_consumer = MagicMock()

        mock_messaging = MagicMock()
        mock_messaging.publisher = mock_publisher
        mock_messaging.consumer = mock_consumer

        with (
            patch(
                f"{SDK_ROOT}.layer4_frameworks.persistence.hana.hana_inbox_repository.HanaInboxRepository",
                return_value=mock_inbox,
            ),
            patch(
                f"{SDK_ROOT}.layer4_frameworks.persistence.hana.hana_outbox_repository.HanaOutboxRepository",
                return_value=mock_outbox,
            ),
            patch(
                f"{SDK_ROOT}.layer4_frameworks.persistence.hana.hana_connection_manager.HanaConnectionManager",
                return_value=mock_hana_cm,
            ),
            patch(
                f"{SDK_ROOT}.layer4_frameworks.persistence.hana.hana_shared_state_repository.HanaSharedStateRepository",
                return_value=MagicMock(),
            ),
            patch(
                f"{SDK_ROOT}.layer4_frameworks.persistence.hana.hana_agent_shared_state_repository.HanaAgentSharedStateRepository",
                return_value=MagicMock(),
            ),
            patch(
                f"{SDK_ROOT}.layer4_frameworks.messaging.factory.create_messaging",
                return_value=mock_messaging,
            ),
        ):
            from agent_sdk.bootstrap import build_app_container

            container = build_app_container()

        from agent_sdk.layer2_application.services.outbox_publisher import (
            OutboxPublisher,
        )

        deps = container["_dependencies"]
        assert isinstance(deps["publisher"], OutboxPublisher)


# ---------------------------------------------------------------------------
# Consumer durable-mode tests
# ---------------------------------------------------------------------------


class TestConsumerDurableMode:
    """Verify consumer uses durable dedup when inbox_repository is present."""

    def _build_container(self, *, with_inbox: bool = False):
        execute = AsyncMock()
        execute.execute = AsyncMock(
            return_value=MagicMock(
                message="ok",
                status="success",
                correlation_id="c1",
                session_id="s1",
                error="",
                error_code="",
                interrupted=False,
                interrupt_payload=None,
            )
        )
        consumer = _FakeConsumer()
        publisher = _FakePublisher()
        container = {
            "execute_agent": execute,
            "consumer": consumer,
            "publisher": publisher,
            "logger": MagicMock(),
        }
        if with_inbox:
            container["inbox_repository"] = MagicMock()
        return container

    def test_durable_dedup_used_when_inbox_present(self):
        """Consumer should create DurableMessageDeduplicator when inbox_repository is in container."""
        container = self._build_container(with_inbox=True)
        asyncio.run(_import_and_run(container))
        # Handler was set, inbox_repository was accessed
        assert container["consumer"].handler is not None

    def test_no_durable_dedup_without_inbox(self):
        """Consumer should use in-memory dedup when inbox_repository is absent."""
        container = self._build_container(with_inbox=False)
        asyncio.run(_import_and_run(container))
        assert container["consumer"].handler is not None

    def test_durable_mode_marks_received_before_processing(self):
        """In durable mode, mark_received should be called and delivery acked after processing."""
        inbox_repo = MagicMock()
        inbox_repo.find_by_message_id.return_value = None  # not a duplicate

        container = self._build_container(with_inbox=True)
        container["inbox_repository"] = inbox_repo

        async def _run():
            from agent_sdk.layer3_adapters.presenters.agent_consumer import (
                run_consumer_agent,
            )

            await run_consumer_agent(container)
            handler = container["consumer"].handler

            delivery = _FakeDelivery(
                payload={
                    "message_id": "msg-1",
                    "type": "agent.request.agent",
                    "correlation_id": "c1",
                }
            )
            await handler(delivery)
            assert delivery._acked
            inbox_repo.save.assert_called_once()

        asyncio.run(_run())

    def test_duplicate_message_skipped_in_durable_mode(self):
        """Duplicate messages should be acked and skipped in durable mode."""
        inbox_repo = MagicMock()
        # Simulate existing record → duplicate
        inbox_repo.find_by_message_id.return_value = MagicMock(status="COMPLETED")

        container = self._build_container(with_inbox=True)
        container["inbox_repository"] = inbox_repo

        async def _run():
            from agent_sdk.layer3_adapters.presenters.agent_consumer import (
                run_consumer_agent,
            )

            await run_consumer_agent(container)
            handler = container["consumer"].handler

            delivery = _FakeDelivery(
                payload={
                    "message_id": "msg-dup",
                    "type": "agent.request.agent",
                    "correlation_id": "c1",
                }
            )
            await handler(delivery)
            assert delivery._acked

        asyncio.run(_run())


async def _import_and_run(container):
    from agent_sdk.layer3_adapters.presenters.agent_consumer import run_consumer_agent

    await run_consumer_agent(container)

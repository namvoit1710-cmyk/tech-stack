"""SA-1570 regression — POST /orchestrate/async must not 500 on the first
message of a brand-new conversation.

The new-conversation branch of ``OrchestrateUseCase.execute_fire_and_forget``
builds an agent-sdk ``Metadata``. Before the fix it passed
``Metadata(conversation_id=...)`` while the vendored agent-sdk ``Metadata``
requires ``conv_id`` -> pydantic ``ValidationError`` -> unhandled by the
422-only controller -> **500** on the very first message ("hi").

These tests pin BOTH the new-conversation branch and the field contract so the
``conv_id``/``conversation_id`` rename cannot silently regress again.
"""
import pytest
from pydantic import ValidationError

from agent_sdk.layer1_domain.entities.agent_shared_state import Metadata
from app.layer2_application.features.orchestrate.use_cases.orchestrate_use_case import (
    OrchestrateInput,
    OrchestrateUseCase,
)


class _FakeUuidBuilder:
    def __init__(self):
        self._n = 0

    def create_uuid7(self):
        self._n += 1
        return f"uuid-{self._n}"


class _FakeSharedStateRepo:
    """Minimal IAgentSharedStateRepository fake — in-memory, records calls."""

    def __init__(self):
        self.saved = []
        self.updated_metadata = []
        self.updated_context = []
        self._store = {}

    def get_conversation_by(self, conv_id):
        return self._store.get(conv_id)

    def save_conversation(self, conversation):
        self.saved.append(conversation)
        self._store[conversation.conv_id] = conversation

    def update_metadata(self, conv_id, metadata):
        self.updated_metadata.append((conv_id, metadata))

    def update_context(self, conv_id, context):
        self.updated_context.append((conv_id, context))


class _FakePublisher:
    def __init__(self):
        self.published = []

    async def publish(self, topic, message):
        self.published.append((topic, message))


def _make_use_case(repo, publisher, settings, logger, monitor):
    return OrchestrateUseCase(
        logger=logger,
        monitor=monitor,
        orchestration_state_repo=repo,
        publisher=publisher,
        uuid_builder=_FakeUuidBuilder(),
        settings=settings,
    )


@pytest.mark.asyncio
async def test_new_conversation_first_message_does_not_500(settings, logger, monitor):
    repo = _FakeSharedStateRepo()
    publisher = _FakePublisher()
    use_case = _make_use_case(repo, publisher, settings, logger, monitor)

    # First message of a brand-new conversation: no conv_id supplied.
    result = await use_case.execute_fire_and_forget(
        OrchestrateInput(message="hi", conv_id="", user_id="u1", user_email="u@example.com")
    )

    # The branch ran to completion (no ValidationError) and persisted a conversation.
    assert len(repo.saved) == 1
    saved = repo.saved[0]

    # 202-equivalent output echoes the newly-minted conversation id.
    assert result.conv_id
    assert result.conv_id == saved.conv_id

    # Metadata was built with the sdk's real field name and carries the id.
    assert isinstance(saved.shared_state.metadata, Metadata)
    assert saved.shared_state.metadata.conv_id == result.conv_id

    # Fire-and-forget: exactly one publish to the orchestrator request topic.
    assert len(publisher.published) == 1
    topic, _message = publisher.published[0]
    assert topic == settings.ORCHESTRATOR_REQUEST_TOPIC


def test_metadata_contract_requires_conv_id_not_conversation_id():
    """Pin the agent-sdk Metadata field the orchestrator depends on.

    If Metadata is renamed conv_id -> conversation_id again (as happened in the
    SA-1107/SA-1679 churn), this fails loudly instead of shipping a 500.
    """
    ok = Metadata(conv_id="c1", checkpoint_id="CP-00")
    assert ok.conv_id == "c1"

    with pytest.raises(ValidationError):
        Metadata(conversation_id="c1", checkpoint_id="CP-00")

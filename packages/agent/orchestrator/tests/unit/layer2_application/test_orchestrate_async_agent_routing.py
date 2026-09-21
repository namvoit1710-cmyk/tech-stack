"""SA-2114 — an /async request's routing `type` + `parameters.agent_id` must reach the
published ``OrchestrationGraphCommand`` so ``OrchestrateGraphUseCase._is_direct_agent_request``
can see them. Regression guard for the SA-1477 dead-code bug where a merged
``command_parameters`` dict was built but never actually used to construct the command."""

import pytest

from app.layer1_domain.entities.exceptions import RequestValidationAppError
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
    def __init__(self):
        self.saved = []
        self._store = {}

    def get_conversation_by(self, conv_id):
        return self._store.get(conv_id)

    def save_conversation(self, conversation):
        self.saved.append(conversation)
        self._store[conversation.conv_id] = conversation

    def update_metadata(self, conv_id, metadata):
        pass

    def update_context(self, conv_id, context):
        pass


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
async def test_agent_type_and_agent_id_reach_the_published_command(settings, logger, monitor):
    repo = _FakeSharedStateRepo()
    publisher = _FakePublisher()
    use_case = _make_use_case(repo, publisher, settings, logger, monitor)

    await use_case.execute_fire_and_forget(
        OrchestrateInput(
            message="route me",
            conv_id="",
            user_id="u1",
            user_email="u@example.com",
            type="AGENT",
            parameters={"agent_id": "agent-approval"},
        )
    )

    assert len(publisher.published) == 1
    _topic, message = publisher.published[0]
    assert message["parameters"]["type"] == "AGENT"
    assert message["parameters"]["agent_id"] == "agent-approval"


@pytest.mark.asyncio
async def test_default_routing_type_absent_reaches_the_published_command(settings, logger, monitor):
    repo = _FakeSharedStateRepo()
    publisher = _FakePublisher()
    use_case = _make_use_case(repo, publisher, settings, logger, monitor)

    await use_case.execute_fire_and_forget(
        OrchestrateInput(message="hi", conv_id="", user_id="u1", user_email="u@example.com")
    )

    assert len(publisher.published) == 1
    _topic, message = publisher.published[0]
    assert message["parameters"]["type"] == ""
    assert message["parameters"]["agent_id"] == ""


@pytest.mark.asyncio
async def test_agent_type_without_agent_id_is_rejected_before_publish(settings, logger, monitor):
    repo = _FakeSharedStateRepo()
    publisher = _FakePublisher()
    use_case = _make_use_case(repo, publisher, settings, logger, monitor)

    with pytest.raises(RequestValidationAppError):
        await use_case.execute_fire_and_forget(
            OrchestrateInput(
                message="route me",
                conv_id="",
                user_id="u1",
                user_email="u@example.com",
                type="AGENT",
            )
        )

    assert publisher.published == []

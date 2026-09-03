from agent_sdk.layer2_application.interfaces.agent_pipeline import IAgentPipeline
from agent_sdk.layer2_application.interfaces.chat_completion_service import (
    IChatCompletionService,
)
from agent_sdk.layer2_application.interfaces.llm_service import ILLMService
from agent_sdk.layer2_application.interfaces.observability import ILogger
from agent_sdk.layer2_application.interfaces.workflow_event_emitter import (
    IWorkflowEventEmitter,
)


class DummyPipeline:
    async def execute(self, request):
        from agent_sdk.layer1_domain.entities.agent_response import AgentResponse

        return AgentResponse(message="ok")


class DummyLogger:
    def info(self, message, **kwargs):
        pass

    def error(self, message, **kwargs):
        pass

    def warning(self, message, **kwargs):
        pass

    def debug(self, message, **kwargs):
        pass


class DummyLLMService:
    """Minimal implementation conforming to ILLMService protocol."""

    def get_chat_client(self):
        return None


class DummyChatCompletionService(DummyLLMService):
    async def get_chat_completion(
        self, system_prompt: str, user_prompt: str, json_mode: bool = True
    ):
        return ""


def test_dummy_pipeline_satisfies_protocol():
    pipeline: IAgentPipeline = DummyPipeline()
    assert hasattr(pipeline, "execute")


def test_dummy_logger_satisfies_protocol():
    logger: ILogger = DummyLogger()
    logger.info("test")


def test_dummy_llm_service_satisfies_protocol():
    """ILLMService protocol conformance check."""
    svc: ILLMService = DummyLLMService()
    assert hasattr(svc, "get_chat_client")


def test_illm_service_protocol_does_not_require_get_chat_completion():
    svc: ILLMService = DummyLLMService()
    assert not hasattr(svc, "get_chat_completion")


def test_dummy_chat_completion_service_satisfies_protocol():
    """IChatCompletionService protocol conformance check."""
    svc: IChatCompletionService = DummyChatCompletionService()
    assert hasattr(svc, "get_chat_client")
    assert hasattr(svc, "get_chat_completion")


def test_ichat_completion_service_extends_illm_service():
    assert ILLMService in IChatCompletionService.__mro__


def test_iworkflow_event_emitter_protocol_exists():
    """IWorkflowEventEmitter must be importable and have an emit method."""
    assert hasattr(IWorkflowEventEmitter, "emit")


def test_iworkflow_event_emitter_is_runtime_checkable():
    """IWorkflowEventEmitter must support isinstance checks (runtime_checkable)."""

    class DummyEmitter:
        async def emit(self, event, *, state=None, node_id=None, topic=None):
            pass

        async def emit_ui_event(self, event, *, state=None, node_id=None, topic=None):
            pass

        async def emit_chat_enabled(
            self,
            *,
            conversation_id="",
            payload=None,
            state=None,
            node_id=None,
            topic=None,
        ):
            pass

        async def emit_chat_disabled(
            self,
            *,
            conversation_id="",
            payload=None,
            state=None,
            node_id=None,
            topic=None,
        ):
            pass

        async def emit_orchestration_event(
            self, event, *, state=None, node_id=None, topic=None
        ):
            pass

    dummy = DummyEmitter()
    assert isinstance(dummy, IWorkflowEventEmitter)

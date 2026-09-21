from app.layer1_domain.enums.workflow_status import WorkflowStatus

"""Shared fixtures for orchestrator tests.

Provides dummy implementations of all L2 interfaces so that
use cases and domain services can be tested in isolation.
"""

# Set test-friendly environment vars to avoid initializing external LLM providers.
import os
os.environ.setdefault("LLM_MODEL", "")
os.environ.setdefault("LLM_PROVIDER", "mock")


import asyncio
import inspect
import sys
import types

import pytest


def _ensure_module(name: str) -> types.ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        sys.modules[name] = module
    return module


def _install_optional_dependency_stubs() -> None:
    """Stub optional LangGraph/LangChain modules for unit tests."""
    try:
        import langchain_core.runnables  # type: ignore  # noqa: F401
    except ModuleNotFoundError:
        langchain_core = _ensure_module("langchain_core")
        runnables = _ensure_module("langchain_core.runnables")
        runnables.RunnableConfig = dict
        langchain_core.runnables = runnables

    try:
        import langgraph.checkpoint.base  # type: ignore  # noqa: F401
        import langgraph.checkpoint.memory  # type: ignore  # noqa: F401
        import langgraph.checkpoint.serde.jsonplus  # type: ignore  # noqa: F401
        import langgraph.graph  # type: ignore  # noqa: F401
        import langgraph.graph.state  # type: ignore  # noqa: F401
        import langgraph.types  # type: ignore  # noqa: F401
    except ModuleNotFoundError:
        langgraph = _ensure_module("langgraph")
        checkpoint = _ensure_module("langgraph.checkpoint")
        base = _ensure_module("langgraph.checkpoint.base")
        serde = _ensure_module("langgraph.checkpoint.serde")
        jsonplus = _ensure_module("langgraph.checkpoint.serde.jsonplus")
        memory = _ensure_module("langgraph.checkpoint.memory")
        graph = _ensure_module("langgraph.graph")
        graph_state = _ensure_module("langgraph.graph.state")
        langgraph_types = _ensure_module("langgraph.types")

        class BaseCheckpointSaver:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        class JsonPlusSerializer:
            def dumps(self, value):
                return value

            def loads(self, value):
                return value

        class MemorySaver:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def invoke(self, *args, **kwargs):
                return {}

            async def ainvoke(self, *args, **kwargs):
                return {}

        class CompiledStateGraph:
            def invoke(self, *args, **kwargs):
                return {}

            async def ainvoke(self, *args, **kwargs):
                return {}

        class StateGraph:
            def __init__(self, *args, **kwargs):
                self.nodes = {}

            def add_node(self, name, node):
                self.nodes[name] = node

            def add_edge(self, *args, **kwargs):
                return None

            def add_conditional_edges(self, *args, **kwargs):
                return None

            def compile(self, *args, **kwargs):
                return CompiledStateGraph()

        base.WRITES_IDX_MAP = {}
        base.BaseCheckpointSaver = BaseCheckpointSaver
        base.ChannelVersions = dict
        base.Checkpoint = dict
        base.CheckpointMetadata = dict
        base.CheckpointTuple = dict
        jsonplus.JsonPlusSerializer = JsonPlusSerializer
        memory.MemorySaver = MemorySaver
        graph.StateGraph = StateGraph
        graph.START = "START"
        graph.END = "END"
        graph_state.CompiledStateGraph = CompiledStateGraph
        graph_state.RunnableConfig = dict
        langgraph_types.StateSnapshot = dict

        checkpoint.base = base
        checkpoint.memory = memory
        checkpoint.serde = serde
        serde.jsonplus = jsonplus
        graph.state = graph_state
        langgraph.checkpoint = checkpoint
        langgraph.graph = graph
        langgraph.types = langgraph_types


_install_optional_dependency_stubs()


def _install_agent_sdk_stubs() -> None:
    """Create minimal stub modules for `agent_sdk` used in app imports."""
    try:
        import agent_sdk  # type: ignore
    except ModuleNotFoundError:
        agent_sdk_mod = _ensure_module("agent_sdk")
        layer1 = _ensure_module("agent_sdk.layer1_domain")
        entities = _ensure_module("agent_sdk.layer1_domain.entities")

        # Provide a minimal Settings base class to be subclassed by app Settings
        class _StubSettings:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)
        agent_sdk_mod.Settings = _StubSettings

        # agent_state stub with AgentBaseState
        agent_state = _ensure_module("agent_sdk.layer1_domain.entities.agent_state")
        class AgentBaseState:
            def __init__(self, *args, **kwargs):
                pass
        agent_state.AgentBaseState = AgentBaseState

        # agent_shared_state stub with required classes
        shared = _ensure_module("agent_sdk.layer1_domain.entities.agent_shared_state")
        class Metadata:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)
                self.status = getattr(self, "status", None)

        class GlobalContext:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        class Task:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)
                self.status = getattr(self, "status", None)

        class AgentSharedState:
            def __init__(self, metadata=None, context=None, executions=None):
                self.metadata = metadata
                self.context = context
                self.executions = executions or []

        class Conversation:
            def __init__(self, conv_id=None, user_id=None, shared_state=None):
                self.conv_id = conv_id
                self.user_id = user_id
                self.shared_state = shared_state
                self.conversation_id = getattr(self, "conversation_id", conv_id)
                self.created_at = 0

        shared.Metadata = Metadata
        shared.GlobalContext = GlobalContext
        shared.Task = Task
        shared.AgentSharedState = AgentSharedState
        shared.Conversation = Conversation


import os

# Ensure the local orchestrator package is preferred for top-level imports like `main`
_orchestrator_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if os.path.isdir(_orchestrator_dir):
    sys.path.insert(0, _orchestrator_dir)

# Preload the local orchestrator `main.py` as top-level `main` module
try:
    import importlib.util
    orchestrator_main = os.path.abspath(os.path.join(_orchestrator_dir, "main.py"))
    if os.path.isfile(orchestrator_main):
        spec = importlib.util.spec_from_file_location("main", orchestrator_main)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore
        sys.modules["main"] = module
except Exception:
    pass

# Prefer the local editable `agent-sdk` package in the repo when available
_agent_sdk_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agent-sdk"))
if os.path.isdir(_agent_sdk_dir):
    sys.path.insert(0, _agent_sdk_dir)

_install_agent_sdk_stubs()

# If the real agent_sdk is available, patch its build_app_container to ensure
# the orchestrator bootstrap finds a hana_connection_manager dependency.
try:
    import agent_sdk
    _orig_build = getattr(agent_sdk, "build_app_container", None)
    if _orig_build is not None:
        def _patched_build(*args, **kwargs):
            container = _orig_build(*args, **kwargs)
            deps = container.get("_dependencies") or {}
            if "hana_connection_manager" not in deps:
                deps["hana_connection_manager"] = object()
                container["_dependencies"] = deps
            return container
        agent_sdk.build_app_container = _patched_build
except Exception:
    pass

from app.layer1_domain.entities.orchestrator.orchestration_state import OrchestrationState


class _DummyFields:
    __dataclass_fields__ = {}
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


OrchestrationCategory = type("OrchestrationCategory", (_DummyFields,), {})
OrchestrationContext = type("OrchestrationContext", (_DummyFields,), {})
OrchestrationPermission = type("OrchestrationPermission", (_DummyFields,), {})
OrchestrationPlanning = type("OrchestrationPlanning", (_DummyFields,), {})
OrchestrationResult = type("OrchestrationResult", (_DummyFields,), {})
OrchestrationRuntime = type("OrchestrationRuntime", (_DummyFields,), {})
OrchestrationWorkflow = type("OrchestrationWorkflow", (_DummyFields,), {})
from app.layer4_frameworks.config.app_config import Settings
from agent_sdk.layer1_domain.entities.agent_shared_state import AgentSharedState, Conversation, GlobalContext, Metadata, Task


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: run async test via test harness event loop")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None

    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
    }
    asyncio.run(pyfuncitem.obj(**kwargs))
    return True


# ── Dummy Logger / Monitor ──

class DummyLogger:
    def info(self, msg, *args, **kwargs): pass
    def error(self, msg, *args, **kwargs): pass
    def warning(self, msg, *args, **kwargs): pass
    def debug(self, msg, *args, **kwargs): pass


class DummyMonitor:
    def track(self, metric, value): pass
    def increment(self, metric, tags=None): pass


def build_orchestration_state(
    *,
    context=None,
    planning=None,
    result=None,
    permission=None,
    category=None,
    workflow=None,
    runtime=None,
):
    return OrchestrationState(
        context=OrchestrationContext(**(context or {})),
        planning=OrchestrationPlanning(**(planning or {})),
        result=OrchestrationResult(**(result or {})),
        permission=OrchestrationPermission(**(permission or {})),
        category=OrchestrationCategory(**(category or {})),
        workflow=OrchestrationWorkflow(**(workflow or {})),
        runtime=OrchestrationRuntime(**(runtime or {})),
    )


def build_shared_conversation(
    *,
    conversation_id: str,
    user_id: str,
    conversation_context: str,
    run_id: str,
    workflow_id: str,
    current_agent: str,
    task_id: str = "task_1",
    task_goal: str = "Execute workflow",
    task_execute_by: str = "workflow",
):
    conversation = Conversation(
        conv_id=conversation_id,
        user_id=user_id,
        shared_state=AgentSharedState(
            metadata=Metadata(
                conv_id=conversation_id,
                main_conv_id=conversation_id,
                checkpoint_id="CP-00",
                parent_checkpoint=None,
                last_event_id=None,
                correlation_id=run_id,
                current_task_id=task_id,
                current_run_id=run_id,
                current_agent=current_agent,
            ),
            context=GlobalContext(
                conversation_context=conversation_context,
            ),
            executions=[
                Task(
                    task_id=task_id,
                    agent=task_execute_by,
                    name=task_goal,
                    parent_task_id=None,
                    next_task_id=None,
                    input_data={},
                    output_data={},
                    error_detail=None,
                    workflow_id=workflow_id,
                    agent_id=current_agent,
                )
            ],
        ),
    )
    object.__setattr__(
        conversation.shared_state.context,
        "extracted_data",
        {
            "run_id": run_id,
            "workflow_id": workflow_id,
        },
    )
    object.__setattr__(
        conversation.shared_state,
        "is_completed",
        lambda: str(conversation.shared_state.metadata.status) == "MetadataStatus.COMPLETED"
        or str(conversation.shared_state.metadata.status) == "COMPLETED",
    )
    object.__setattr__(
        conversation.shared_state,
        "has_incomplete_execution",
        lambda: any(str(task.status) not in {"TaskStatus.SUCCESS", "success"} for task in conversation.shared_state.executions),
    )
    return conversation


# ── Dummy Repos ──

class DummyConversationRepo:
    def __init__(self):
        self._store = {}

    def save(self, conversation):
        self._store[conversation.conversation_id] = conversation

    def get_by_id(self, conversation_id):
        return self._store.get(conversation_id)

    def list_by_user(self, user_id):
        return [c for c in self._store.values() if c.user_id == user_id]

    def get_conversations_for_user(self, user_id, tenant_id="", *, limit=10, offset=0):
        conversations = sorted(
            self.list_by_user(user_id),
            key=lambda conversation: (
                conversation.created_at,
                conversation.conversation_id,
            ),
            reverse=True,
        )
        return conversations[offset:offset + limit]

    def count_conversations_for_user(self, user_id, tenant_id=""):
        return len(self.list_by_user(user_id))

    def delete(self, conversation_id):
        self._store.pop(conversation_id, None)

    def add_message(self, conversation_id, role, content, *, user_id="anonymous"):
        pass

    def update_active_agents(self, conversation_id, agent_id):
        pass

    def purge_expired(self, **kwargs):
        return 0


class DummySubConversationRelationRepo:
    def __init__(self):
        self._relations = {}

    def save_relation(
        self,
        conversation_id,
        parent_conversation_id,
        context_id,
        node_id,
    ):
        self._relations[(parent_conversation_id, node_id)] = {
            "conversation_id": conversation_id,
            "parent_conversation_id": parent_conversation_id,
            "context_id": context_id,
            "node_id": node_id,
        }

    def get_sub_conversation_id(self, parent_conversation_id, node_id):
        rel = self._relations.get((parent_conversation_id, node_id))
        return rel["conversation_id"] if rel else None

    def delete_relations_for_main(self, parent_conversation_id):
        keys = [k for k in self._relations.keys() if k[0] == parent_conversation_id]
        for k in keys:
            self._relations.pop(k, None)

    def get_root_conversation_id(self, conversation_id):
        # For dummy, assume no nesting, return as is
        return conversation_id


class DummyConversationContextRepo:
    def __init__(self):
        self._contexts = {}

    def save_context(
        self,
        context_id,
        run_id,
        workflow_id,
        user_id,
        tenant_id,
    ):
        self._contexts[context_id] = {
            "context_id": context_id,
            "run_id": run_id,
            "workflow_id": workflow_id,
            "workflow_name": "",
            "workflow_status": WorkflowStatus.PENDING.value,
            "status": "ACTIVATED",
            "user_id": user_id,
            "tenant_id": tenant_id,
        }

    def get_context(self, context_id):
        return self._contexts.get(context_id)

    def get_context_by_run_id(self, run_id):
        for context in self._contexts.values():
            if context["run_id"] == run_id:
                return context
        return None

    def get_context_by_run_id_and_node_id(self, run_id, node_id):
        return None

    def update_context(self, context_id, workflow_name):
        if context_id in self._contexts:
            self._contexts[context_id]["workflow_name"] = workflow_name

    def update_workflow_status(self, context_id, workflow_status):
        if context_id in self._contexts:
            self._contexts[context_id]["workflow_status"] = workflow_status

    def update_status(self, context_id, status):
        if context_id in self._contexts:
            self._contexts[context_id]["status"] = status

    def get_contexts_for_user(self, user_id, tenant_id, *, limit=10, offset=0):
        contexts = sorted(
            [
                ctx for ctx in self._contexts.values()
                if ctx["user_id"] == user_id and ctx["tenant_id"] == tenant_id
            ],
            key=lambda ctx: (
                ctx.get("updated_at", 0),
                ctx.get("created_at", 0),
                ctx.get("context_id", ""),
            ),
            reverse=True,
        )
        return contexts[offset:offset + limit]

    def count_contexts_for_user(self, user_id, tenant_id):
        return sum(
            1
            for ctx in self._contexts.values()
            if ctx["user_id"] == user_id and ctx["tenant_id"] == tenant_id
        )

    def delete_context(self, context_id):
        self._contexts.pop(context_id, None)

    def get_context_by_conversation_id(self, conversation_id):
        return self._contexts.get(conversation_id)


class DummyConversationRelationRepo:
    """Legacy dummy for backward compatibility - maintains old interface."""
    def __init__(self):
        self._relations = {}

    def save_relation(
        self,
        conversation_id,
        parent_conversation_id,
        context_id,
        workflow_id,
        node_id,
        user_id,
        tenant_id,
    ):
        self._relations[(parent_conversation_id, node_id)] = conversation_id

    def get_sub_conversation_id(self, parent_conversation_id, node_id):
        return self._relations.get((parent_conversation_id, node_id))

    def get_relations_for_main(self, parent_conversation_id):
        return [
            {"conversation_id": v, "parent_conversation_id": parent_conversation_id, "node_id": k[1]}
            for (p, k), v in self._relations.items() if p == parent_conversation_id
        ]

    def delete_relations_for_main(self, parent_conversation_id):
        keys = [k for k in self._relations.keys() if k[0] == parent_conversation_id]
        for k in keys:
            self._relations.pop(k, None)

    def get_root_conversation_id(self, conversation_id):
        # For dummy, assume no nesting, return as is
        return conversation_id


class DummyUserProfileRepo:
    def get(self, user_id, tenant_id):
        return None

    def save(self, profile):
        pass

    def delete(self, user_id, tenant_id):
        pass


class DummyUserHistoryRepo:
    def get_history(self, user_id, tenant_id):
        return None

    def add_entry(self, entry):
        pass

    def get_recent(self, user_id, tenant_id, entry_type, limit=10):
        return []

    def purge_expired(self, tenant_id):
        return 0


class DummyJourneyRepo:
    def record(self, event):
        pass

    def get_by_user(self, user_id, tenant_id, *, event_type=None, limit=100):
        return []

    def get_by_conversation(self, conversation_id, tenant_id):
        return []

    def count_by_user(self, user_id, tenant_id, event_type=None):
        return 0


class DummyAuditLogRepo:
    def record(self, entry):
        pass

    def get_by_user(self, user_id, tenant_id, *, limit=100):
        return []

    def get_by_conversation(self, conversation_id, tenant_id):
        return []

    def get_by_resource(self, resource_type, resource_id, tenant_id, *, limit=100):
        return []


class DummyOrchestrationAuditLogRepo:
    def record(self, entry):
        pass

    def get_by_conversation(self, conversation_id):
        return []


# ── Dummy Providers ──

class DummyPermissionProvider:
    def get_user_permissions(self, user_id):
        return {
            "roles": ["user"],
            "allowed_agents": [],
            "subscription_tier": "standard",
            "resource_quotas": {"max_requests_per_hour": 100},
        }

    def get_user_features(self, user_email, authorization_header=""):
        return {
            "email": user_email,
            "features": ["basic_chat", "workflow_access"],
            "feature_flags": {"beta_tools": False, "priority_support": False},
        }


class DummyLLMProvider:
    def classify_intent(self, message, context):
        return {
            "domain": "general",
            "action": "chat",
            "target_action": "chat",
            "entities": {},
            "confidence": 0.5,
            "parameters": {},
        }


class DummyEventPublisher:
    def __init__(self):
        self.published = []

    def publish(self, topic, event):
        self.published.append((topic, event))


class DummyPushGatewayNotifier:
    def __init__(self):
        self.sent = []

    async def send_notification(self, key, data, *, is_final=True):
        self.sent.append({"key": key, "data": data, "is_final": is_final})

    async def close(self):
        pass


class DummyMessagePublisher:
    def __init__(self):
        self.published = []
        self._responses = {}

    async def publish_agent_request(self, message):
        self.published.append(message)
        cid = message.correlation_id
        self._responses[cid] = {
            "correlation_id": cid,
            "success": True,
            "message": f"[Mock] processed",
            "result": {"message": "[Mock] processed"},
        }
        return cid

    def pop_response(self, correlation_id):
        return self._responses.pop(correlation_id, None)

    async def publish_orchestrator_request(self, message):
        self.published.append(message)
        cid = message.correlation_id
        self._responses[cid] = {
            "correlation_id": cid,
            "success": True,
            "message": f"[Mock] orchestrator processed",
            "result": {"message": "[Mock] orchestrator processed"},
        }
        return cid


# ── Fixtures ──

@pytest.fixture
def settings():
    return Settings(INFRA_MODE="mock", LLM_PROVIDER="mock")


@pytest.fixture
def logger():
    return DummyLogger()


@pytest.fixture
def monitor():
    return DummyMonitor()


@pytest.fixture
def conversation_repo():
    return DummyConversationRepo()


@pytest.fixture
def user_profile_repo():
    return DummyUserProfileRepo()


@pytest.fixture
def user_history_repo():
    return DummyUserHistoryRepo()


@pytest.fixture
def journey_repo():
    return DummyJourneyRepo()


@pytest.fixture
def audit_log_repo():
    return DummyAuditLogRepo()


@pytest.fixture
def permission_provider():
    return DummyPermissionProvider()


@pytest.fixture
def llm_provider():
    return DummyLLMProvider()


@pytest.fixture
def message_publisher():
    return DummyMessagePublisher()


@pytest.fixture
def push_gateway_notifier():
    return DummyPushGatewayNotifier()

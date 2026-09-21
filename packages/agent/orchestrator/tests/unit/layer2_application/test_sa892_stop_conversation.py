"""SA-892 (N5/N10 + route-via-executor-tool): POST /orchestrate/stop stops BOTH
halves of a conversation.

T1 use case publishes an agent.control cancel keyed by conv_id.
T2 use case STOPS the run resolved from shared-state Metadata.current_run_id (the
   authoritative primary source) by DISPATCHING a workflow_stop STEP to the executor
   runtime — NOT via an orchestrator-owned WCP HTTP client.
T3 partial failure: the agent-control publish failing still dispatches the stop
   steps; one stop-step dispatch failing does not stop the others.
T4 route: POST /orchestrate/stop -> 202 with the contract JSON.

ROUTE-VIA-EXECUTOR-TOOL (this change): the run cancel no longer calls
``workflow_client.cancel_run(run_id)`` (an orchestrator-owned WCP HTTP client).

    PRE-CHANGE behavior (for contrast): ``_cancel_runs`` called
    ``await self.workflow_client.cancel_run(run_id)`` once per resolved run_id, and
    the use case took a ``workflow_client`` constructor dependency.

    POST-CHANGE behavior (asserted below): for each resolved run_id the stop
    PUBLISHES a ``workflow_stop`` STEP BATCH to the executor's step-intake topic
    (``settings.EXECUTOR_TOPIC``), mirroring the business-agent's
    ``send_task_to_executor`` envelope: ``type=agent.request.executor``,
    ``payload.type=step_batch``, ``payload.to=executor.runtime`` and a single step
    whose ``execute_by == settings.WORKFLOW_STOP_EXECUTE_BY`` and
    ``input_data == {"run_id": run_id}``. The executor resolves the capability to the
    registered ``workflow_stop`` tool and performs the control-plane cancel. The
    orchestrator no longer holds a WCP client for the stop path (see
    ``test_stop_use_case_has_no_workflow_client_dependency``).

N10 run resolution: the broken control-plane lookup (``list_runs_by_conversation``)
is removed. Runs now come from the authoritative agent shared state
(``Metadata.current_run_id``) unioned with the orchestrator's in-process
``conv_id -> set[run_id]`` index.
"""

import importlib.util
import inspect
import os
from types import SimpleNamespace

import httpx
import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    GlobalContext,
    Metadata,
    Task,
    TaskStatus,
)
from agent_sdk.layer1_domain.entities.conversation_context import (
    MessageHistory,
    TaskHistory,
)
from agent_sdk.layer1_domain.value_objects.agent_control import (
    AGENT_CANCEL_ACTION,
    AGENT_CONTROL_MESSAGE_TYPE,
)
from agent_sdk.layer4_frameworks.logging.standard import StandardLogger
from app.layer4_frameworks.stop_targets import build_default_stop_targets
from app.layer2_application.features.orchestrate.use_cases.stop_conversation_use_case import (
    StopConversationInput,
    StopConversationUseCase,
)
from app.layer3_adapters.controllers.restful.v1.authentication import require_bearer_token


# SA-892: the workflow-stop capability id under test. A UUID form (proves the value
# is read from settings, and reflects the real registry-data ``tool.<uuid>`` the
# executor's validator/resolver require) rather than the readable default.
_STOP_EXECUTE_BY = "tool.11111111-1111-1111-1111-111111111111"
_EXECUTOR_TOPIC = "executor.topic"


# --- fakes -----------------------------------------------------------------

def _is_step_batch(message) -> bool:
    payload = (message or {}).get("payload") or {}
    return payload.get("type") == "step_batch"


def _step_run_ids(message) -> set[str]:
    payload = (message or {}).get("payload") or {}
    ids = set()
    for step in payload.get("steps", []) or []:
        run_id = (step.get("input_data") or {}).get("run_id")
        if run_id:
            ids.add(run_id)
    return ids


class FakePublisher:
    """Records every publish. Can fail everything (``fail``), only the agent.control
    fan-out (``fail_control``), or only the workflow_stop step batches carrying a
    given run_id (``fail_run_ids``) — the last two model the two independent halves
    now sharing one transport."""

    def __init__(self, fail: bool = False, *, fail_control: bool = False, fail_run_ids=None):
        self.fail = fail
        self.fail_control = fail_control
        self.fail_run_ids = set(fail_run_ids or [])
        self.calls = []  # list of (topic, message, key)

    async def publish(self, topic, message=None, key=None):
        self.calls.append((topic, message, key))
        if self.fail:
            raise ConnectionError("broker unreachable")
        is_step_batch = _is_step_batch(message)
        if self.fail_control and not is_step_batch:
            raise ConnectionError("broker unreachable")
        if is_step_batch and (_step_run_ids(message) & self.fail_run_ids):
            raise ValueError((502, "stop dispatch refused"))

    async def close(self):
        pass


class SpyWorkflowClient:
    """A WCP client spy. The stop use case no longer takes a workflow_client, so this
    exists ONLY to prove ``cancel_run`` is never reachable/invoked from the stop path
    (contrast with the pre-change behavior)."""

    def __init__(self):
        self.cancelled = []
        self.list_calls = []

    async def cancel_run(self, run_id):
        self.cancelled.append(run_id)
        return {"status": "CANCELLED", "run_id": run_id}

    async def list_runs_by_conversation(self, conversation_id):
        self.list_calls.append(conversation_id)
        return []


class FakeSharedStateRepo:
    """SA-892: fake IAgentSharedStateRepository exposing get_metadata,
    get_conversation, and update_task.

    get_metadata returns a real Metadata carrying current_run_id (the authoritative
    active run for the conversation), or None, or raises get_error. get_conversation
    returns a conversation carrying the recorded tenant, current_message_id, and
    executions (used by the task-scoped stop). update_task mutates the matching
    task's fields (task-scoped stop writes status=CANCELLED here).
    """

    def __init__(
        self,
        current_run_id=None,
        *,
        get_error=None,
        update_error=None,
        conv_tenant=None,
        executions=None,
        current_message_id="",
        context=None,
        update_task_error=None,
    ):
        self._current_run_id = current_run_id
        self._get_error = get_error
        self._update_error = update_error
        # SA-892 (L2): tenant recorded on the conversation (None -> not resolvable).
        self._conv_tenant = conv_tenant
        # SA-892 (task-scoped stop): executions + the conversation's current message.
        self._executions = executions
        self._current_message_id = current_message_id
        # SA-892 (conversation_context source): the message->task_ids index the
        # task-scoped stop scopes by (GlobalContext with conversation_context).
        self._context = context
        self._update_task_error = update_task_error
        self.get_metadata_calls = []
        self.get_conversation_calls = []
        self.update_metadata_calls = []  # list of (conv_id, metadata)
        self.update_task_calls = []  # list of (conv_id, task_id, kwargs)
        self._metadata = None

    def get_conversation(self, conv_id):
        # SA-892 (L2): the tenant resolver reads the owning tenant off the
        # Conversation; the task-scoped stop reads executions + current_message_id.
        # A repo with neither tenant nor executions returns None so the stop
        # proceeds best-effort (tenant unverified, nothing to cancel).
        self.get_conversation_calls.append(conv_id)
        if self._conv_tenant is None and self._executions is None:
            return None
        metadata = Metadata(
            conv_id=conv_id,
            checkpoint_id="CP-01",
            current_message_id=self._current_message_id,
        )
        shared_state = SimpleNamespace(
            metadata=metadata,
            executions=list(self._executions or []),
            context=self._context,
        )
        return SimpleNamespace(tenant_id=self._conv_tenant, shared_state=shared_state)

    def get_metadata(self, conv_id):
        self.get_metadata_calls.append(conv_id)
        if self._get_error is not None:
            raise self._get_error
        if self._current_run_id is None and self._metadata is None:
            return None
        if self._metadata is None:
            self._metadata = Metadata(
                conv_id=conv_id,
                checkpoint_id="CP-01",
                current_run_id=self._current_run_id,
            )
        return self._metadata

    def update_metadata(self, conv_id, metadata):
        self.update_metadata_calls.append((conv_id, metadata))
        if self._update_error is not None:
            raise self._update_error
        self._metadata = metadata

    def update_task(self, conv_id, task_id, **kwargs):
        self.update_task_calls.append((conv_id, task_id, kwargs))
        if self._update_task_error is not None:
            raise self._update_task_error
        for task in self._executions or []:
            if task.id == task_id:
                for key, value in kwargs.items():
                    setattr(task, key, value)
        return None


class FakeRunIndex:
    """SA-892 N10: fake conv_id -> set[run_id] index."""

    def __init__(self, mapping=None, *, get_error=None):
        self._mapping = {k: set(v) for k, v in (mapping or {}).items()}
        self._get_error = get_error
        self.recorded = []  # list of (conv_id, run_id)

    def record(self, conv_id, run_id):
        self.recorded.append((conv_id, run_id))
        self._mapping.setdefault(conv_id, set()).add(run_id)

    def get_run_ids(self, conv_id):
        if self._get_error is not None:
            raise self._get_error
        return set(self._mapping.get(conv_id, set()))


class FakeMonitor:
    """SA-892 N11: captures track() calls, summing values per metric."""

    def __init__(self):
        self.calls = []  # list of (metric, value)

    def track(self, metric, value):
        self.calls.append((metric, value))

    def totals(self):
        totals = {}
        for metric, value in self.calls:
            totals[metric] = totals.get(metric, 0) + value
        return totals


class FakeLogger:
    """Captures structured log calls so tests can assert the summary line."""

    def __init__(self):
        self.infos = []   # list of (message, kwargs)
        self.warnings = []

    def info(self, message, **kwargs):
        self.infos.append((message, kwargs))

    def warning(self, message, **kwargs):
        self.warnings.append((message, kwargs))

    def error(self, message, **kwargs):
        pass

    def debug(self, message, **kwargs):
        pass


class FakeUuidBuilder:
    """Deterministic id source so the step-batch envelope ids are assertable."""

    def __init__(self):
        self._n = 0

    def create_uuid7(self):
        self._n += 1
        return f"uuid-{self._n}"


class _Settings:
    AGENT_REQUEST_TOPIC = "business.topic"
    EXECUTOR_TOPIC = _EXECUTOR_TOPIC
    WORKFLOW_STOP_EXECUTE_BY = _STOP_EXECUTE_BY


# --- helpers on the publisher record ---------------------------------------

def _stop_steps(publisher):
    """(topic, message, key, step) for every workflow_stop step dispatched."""
    out = []
    for topic, message, key in publisher.calls:
        if not _is_step_batch(message):
            continue
        for step in message["payload"].get("steps", []):
            out.append((topic, message, key, step))
    return out


def _stopped_run_ids(publisher):
    """run_ids for which a workflow_stop step was dispatched (any order)."""
    ids = []
    for _t, _m, _k, step in _stop_steps(publisher):
        ids.append((step.get("input_data") or {}).get("run_id"))
    return ids


def _control_calls(publisher):
    return [
        (topic, message, key)
        for topic, message, key in publisher.calls
        if not _is_step_batch(message)
    ]


class FakeRegistryClient:
    """SA-892 (capability discovery): resolves a tool id by name; records lookups so
    tests can assert the id is cached (looked up at most once per stop)."""

    def __init__(self, tool_ids=None, *, error=None):
        self._tool_ids = dict(tool_ids or {})
        self._error = error
        self.lookups = []  # list of tool names requested

    async def get_tool_id_by_name(self, name):
        self.lookups.append(name)
        if self._error is not None:
            raise self._error
        return self._tool_ids.get(name)


def _build_use_case(
    publisher,
    *,
    monitor=None,
    conversation_run_index=None,
    orchestration_state_repo=None,
    logger=None,
    uuid_builder=None,
    settings=None,
    agent_registry_client=None,
    stop_targets=None,
):
    logger = logger or StandardLogger()
    settings = settings or _Settings()
    uuid_builder = uuid_builder or FakeUuidBuilder()
    # SA-892 (Ports & Adapters): the composition root builds the default targets and
    # injects them; mirror that here so the tests exercise the real adapters.
    if stop_targets is None:
        stop_targets = build_default_stop_targets(
            publisher=publisher,
            settings=settings,
            logger=logger,
            agent_registry_client=agent_registry_client,
            uuid_builder=uuid_builder,
            conversation_run_index=conversation_run_index,
            orchestration_state_repo=orchestration_state_repo,
        )
    # SA-892 (post Ports & Adapters cleanup): the use case only needs logger,
    # monitor, orchestration_state_repo (authz) and the injected stop_targets.
    # The fan-out collaborators now live in the adapters (built above).
    return StopConversationUseCase(
        logger=logger,
        monitor=monitor,
        orchestration_state_repo=orchestration_state_repo,
        stop_targets=stop_targets,
    )


# --- WCP-client removal (route-via-executor-tool) --------------------------

def test_stop_use_case_has_no_workflow_client_dependency():
    """The stop use case no longer takes a WCP ``workflow_client`` — the run cancel
    is routed through the executor tool instead. (Pre-change, the constructor took a
    ``workflow_client`` and ``_cancel_runs`` called ``workflow_client.cancel_run``.)"""
    params = inspect.signature(StopConversationUseCase.__init__).parameters
    assert "workflow_client" not in params
    # the use case holds no WCP client attribute and never calls it on the run path
    src = inspect.getsource(StopConversationUseCase)
    assert "self.workflow_client" not in src


# --- capability discovery (execute_by from the registry BY NAME) -----------

class _SettingsDiscover:
    """No WORKFLOW_STOP_EXECUTE_BY override -> the stop must DISCOVER the
    workflow_stop tool id from the registry by name."""

    AGENT_REQUEST_TOPIC = "business.topic"
    EXECUTOR_TOPIC = _EXECUTOR_TOPIC
    WORKFLOW_STOP_EXECUTE_BY = ""
    WORKFLOW_STOP_TOOL_NAME = "workflow_stop"


_DISCOVERED_TOOL_ID = "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_stop_discovers_workflow_stop_execute_by_from_registry_by_name():
    """SA-892 (capability discovery): with no override, the stop resolves the
    workflow_stop tool's ``tool.<uuid>`` from the registry BY NAME and uses it as the
    step ``execute_by``. The id is resolved ONCE even for multiple runs (cached)."""
    publisher = FakePublisher()
    # two distinct runs: authoritative (shared state) + supplementary (index).
    shared_state = FakeSharedStateRepo(current_run_id="run-A", conv_tenant="t1")
    run_index = FakeRunIndex({"conv-d": {"run-B"}})
    registry = FakeRegistryClient({"workflow_stop": _DISCOVERED_TOOL_ID})
    use_case = _build_use_case(
        publisher,
        settings=_SettingsDiscover(),
        orchestration_state_repo=shared_state,
        conversation_run_index=run_index,
        agent_registry_client=registry,
    )

    result = await use_case.execute(StopConversationInput(conv_id="conv-d"))

    steps = _stop_steps(publisher)
    assert {(s.get("input_data") or {}).get("run_id") for _t, _m, _k, s in steps} == {
        "run-A",
        "run-B",
    }
    # every dispatched step carries the DISCOVERED capability id.
    assert {s["execute_by"] for _t, _m, _k, s in steps} == {
        f"tool.{_DISCOVERED_TOOL_ID}"
    }
    assert sorted(result.runs_cancelled) == ["run-A", "run-B"]
    # resolved by NAME, and only ONCE despite two runs (cached for the stop).
    assert registry.lookups == ["workflow_stop"]
    assert result.errors == []


@pytest.mark.asyncio
async def test_override_short_circuits_registry_lookup():
    """A configured WORKFLOW_STOP_EXECUTE_BY override is used verbatim and the
    registry is NOT consulted (default _Settings carries the override)."""
    publisher = FakePublisher()
    shared_state = FakeSharedStateRepo(current_run_id="run-1", conv_tenant="t1")
    registry = FakeRegistryClient({"workflow_stop": _DISCOVERED_TOOL_ID})
    use_case = _build_use_case(
        publisher,
        orchestration_state_repo=shared_state,
        agent_registry_client=registry,
    )

    result = await use_case.execute(StopConversationInput(conv_id="conv-o"))

    steps = _stop_steps(publisher)
    assert {s["execute_by"] for _t, _m, _k, s in steps} == {_STOP_EXECUTE_BY}
    assert result.runs_cancelled == ["run-1"]
    assert registry.lookups == []  # override wins; no lookup


@pytest.mark.asyncio
async def test_stop_skips_workflow_half_when_capability_unresolved():
    """No override AND the registry has no workflow_stop tool -> the workflow half is
    SKIPPED (no step batches), the reason is recorded, but the agent-side cancel still
    fires (the two halves are independent)."""
    publisher = FakePublisher()
    shared_state = FakeSharedStateRepo(current_run_id="run-1", conv_tenant="t1")
    registry = FakeRegistryClient({})  # workflow_stop not registered -> None
    use_case = _build_use_case(
        publisher,
        settings=_SettingsDiscover(),
        orchestration_state_repo=shared_state,
        agent_registry_client=registry,
    )

    result = await use_case.execute(StopConversationInput(conv_id="conv-u"))

    assert _stop_steps(publisher) == []  # nothing dispatched
    assert result.runs_cancelled == []
    assert any("unresolved" in e for e in result.errors)
    # agent-side cancel is unaffected.
    assert result.agent_cancelled is True
    assert len(_control_calls(publisher)) == 2


@pytest.mark.asyncio
async def test_stop_skips_workflow_half_when_no_registry_client():
    """No override AND no registry client injected -> workflow half skipped, agent
    cancel still fires. (Never raises.)"""
    publisher = FakePublisher()
    shared_state = FakeSharedStateRepo(current_run_id="run-1", conv_tenant="t1")
    use_case = _build_use_case(
        publisher,
        settings=_SettingsDiscover(),
        orchestration_state_repo=shared_state,
        agent_registry_client=None,
    )

    result = await use_case.execute(StopConversationInput(conv_id="conv-n"))

    assert _stop_steps(publisher) == []
    assert result.runs_cancelled == []
    assert result.agent_cancelled is True


# --- T1 --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t1_publishes_agent_control_cancel_keyed_by_conv_id():
    publisher = FakePublisher()
    use_case = _build_use_case(publisher)

    result = await use_case.execute(StopConversationInput(conv_id="conv-1"))

    assert result.agent_cancelled is True
    # SA-892 (N4): the cancel fans out to BOTH agent-sdk consumers — the business
    # topic AND the executor request topic (agent.request.executor) — as two
    # distinct, conv_id-keyed control publishes.
    control = _control_calls(publisher)
    assert len(control) == 2
    topics = {topic for topic, _msg, _key in control}
    assert topics == {"business.topic", "agent.request.executor"}
    for topic, message, key in control:
        assert message["type"] == AGENT_CONTROL_MESSAGE_TYPE
        assert message["action"] == AGENT_CANCEL_ACTION
        assert message["conv_id"] == "conv-1"
        assert key == "conv-1"


def _task(task_id, status):
    # SA-892 (conversation_context source): tasks no longer carry a message id;
    # the message->task_ids link is seeded via _context() below.
    return Task(
        task_id=task_id,
        agent="agent.file",
        name=f"task {task_id}",
        status=status,
    )


def _context(mapping):
    """Build a GlobalContext whose conversation_context registers, per message id,
    the given task ids (the task-scoped stop scopes the cancel by this index)."""
    return GlobalContext(
        conversation_context=[
            MessageHistory(
                message_id=message_id,
                message=f"message {message_id}",
                tasks=[TaskHistory(task_id=tid, knowledge=[]) for tid in task_ids],
            )
            for message_id, task_ids in mapping.items()
        ]
    )


@pytest.mark.asyncio
async def test_stop_cancels_current_message_non_terminal_tasks():
    """SA-892 (task-scoped stop): a Stop marks the CURRENT MESSAGE's non-terminal
    tasks CANCELLED via update_task and does NOT set Metadata.status."""
    publisher = FakePublisher()
    executions = [
        _task("t1", TaskStatus.PROCESSING),  # current message, non-terminal
        _task("t2", TaskStatus.PENDING),      # current message, non-terminal
        _task("t3", TaskStatus.SUCCESS),      # current message, TERMINAL
        _task("t4", TaskStatus.PROCESSING),   # DIFFERENT message
    ]
    shared_state = FakeSharedStateRepo(
        current_run_id="run-1",
        executions=executions,
        current_message_id="msg-A",
        # msg-A owns t1/t2/t3; t4 belongs to msg-B (a different message).
        context=_context({"msg-A": ["t1", "t2", "t3"], "msg-B": ["t4"]}),
    )
    use_case = _build_use_case(publisher, orchestration_state_repo=shared_state)

    result = await use_case.execute(StopConversationInput(conv_id="conv-ts"))

    # only t1 + t2 cancelled: t3 is terminal, t4 belongs to another message.
    assert sorted(result.tasks_cancelled) == ["t1", "t2"]
    cancelled_ids = [
        tid for _c, tid, kw in shared_state.update_task_calls
        if kw.get("status") == TaskStatus.CANCELLED
    ]
    assert sorted(cancelled_ids) == ["t1", "t2"]
    # no Metadata.status write happened at all.
    assert shared_state.update_metadata_calls == []
    # the rest of the stop still ran.
    assert result.agent_cancelled is True
    assert result.runs_cancelled == ["run-1"]
    assert _stopped_run_ids(publisher) == ["run-1"]
    assert result.errors == []


@pytest.mark.asyncio
async def test_stop_does_not_cancel_a_different_messages_tasks():
    """A stop while the ONLY in-flight task belongs to another message cancels
    nothing (per-task scoping — a new message's task is untouched)."""
    publisher = FakePublisher()
    executions = [_task("t9", TaskStatus.PROCESSING)]
    shared_state = FakeSharedStateRepo(
        executions=executions,
        current_message_id="msg-CURRENT",
        # the current message exists but registered no tasks; t9 belongs to another
        # message -> scoping cancels nothing (NOT the cancel-all fallback).
        context=_context({"msg-CURRENT": [], "msg-OTHER": ["t9"]}),
    )
    use_case = _build_use_case(publisher, orchestration_state_repo=shared_state)

    result = await use_case.execute(StopConversationInput(conv_id="conv-diff"))

    assert result.tasks_cancelled == []
    assert shared_state.update_task_calls == []
    assert shared_state.update_metadata_calls == []


@pytest.mark.asyncio
async def test_stop_fallback_cancels_all_non_terminal_when_no_message_id():
    """Fallback: when NO task carries a message_id (older data), all non-terminal
    tasks are cancelled so a stop still stops what it can."""
    publisher = FakePublisher()
    executions = [
        _task("t1", TaskStatus.PROCESSING),
        _task("t2", TaskStatus.FAIL),  # terminal -> skipped
        _task("t3", TaskStatus.PENDING),
    ]
    # No current message id -> not resolvable in conversation_context -> fallback.
    shared_state = FakeSharedStateRepo(executions=executions, current_message_id="")
    use_case = _build_use_case(publisher, orchestration_state_repo=shared_state)

    result = await use_case.execute(StopConversationInput(conv_id="conv-fb"))

    assert sorted(result.tasks_cancelled) == ["t1", "t3"]


@pytest.mark.asyncio
async def test_stop_no_conversation_skips_task_cancel_gracefully():
    """No persisted conversation -> nothing to cancel; no update, no error."""
    publisher = FakePublisher()
    shared_state = FakeSharedStateRepo(current_run_id=None)  # get_conversation -> None
    use_case = _build_use_case(publisher, orchestration_state_repo=shared_state)

    result = await use_case.execute(StopConversationInput(conv_id="conv-none"))

    assert shared_state.update_task_calls == []
    assert result.tasks_cancelled == []
    assert result.errors == []


@pytest.mark.asyncio
async def test_stop_task_cancel_failure_recorded_but_stop_continues():
    """A failure writing CANCELLED on one task is recorded but never aborts the
    rest of the stop."""
    publisher = FakePublisher()
    executions = [_task("t1", TaskStatus.PROCESSING)]
    shared_state = FakeSharedStateRepo(
        current_run_id="run-1",
        executions=executions,
        current_message_id="msg-A",
        update_task_error=ValueError((502, "down")),
    )
    use_case = _build_use_case(publisher, orchestration_state_repo=shared_state)

    result = await use_case.execute(StopConversationInput(conv_id="conv-fail"))

    assert any("cancel task t1 failed" in e for e in result.errors)
    assert result.tasks_cancelled == []
    # the rest of the stop still ran: agent cancelled + run stop dispatched.
    assert result.agent_cancelled is True
    assert result.runs_cancelled == ["run-1"]
    assert _stopped_run_ids(publisher) == ["run-1"]


# --- T2 (primary path: shared-state current_run_id) ------------------------

@pytest.mark.asyncio
async def test_t2_dispatches_workflow_stop_step_for_shared_state_current_run_id():
    """The authoritative active run for the conversation comes from
    Metadata.current_run_id, and a workflow_stop STEP is dispatched to the executor
    for it (mirroring send_task_to_executor). NO WCP cancel_run call happens."""
    publisher = FakePublisher()
    shared_state = FakeSharedStateRepo(current_run_id="run-1")
    use_case = _build_use_case(publisher, orchestration_state_repo=shared_state)

    result = await use_case.execute(StopConversationInput(conv_id="conv-2"))

    # get_metadata is read by the tenant fallback resolver and the run resolver;
    # both target this conversation.
    assert set(shared_state.get_metadata_calls) == {"conv-2"}
    assert result.runs_cancelled == ["run-1"]
    assert result.errors == []

    # Exactly one workflow_stop step, keyed + shaped like send_task_to_executor.
    steps = _stop_steps(publisher)
    assert len(steps) == 1
    topic, message, key, step = steps[0]
    assert topic == _EXECUTOR_TOPIC
    assert key == "conv-2"
    assert message["type"] == "agent.request.executor"
    assert message["conv_id"] == "conv-2"
    assert message["correlation_id"] == "conv-2"
    payload = message["payload"]
    assert payload["type"] == "step_batch"
    assert payload["to"] == "executor.runtime"
    assert payload["from"]  # non-empty producer id
    assert payload["batch_id"] == message["message_id"]
    assert step["execute_by"] == _STOP_EXECUTE_BY
    assert step["input_data"] == {"run_id": "run-1"}


@pytest.mark.asyncio
async def test_t2_no_current_run_and_no_index_dispatches_nothing():
    publisher = FakePublisher()
    shared_state = FakeSharedStateRepo(current_run_id=None)  # metadata w/o run
    use_case = _build_use_case(publisher, orchestration_state_repo=shared_state)

    result = await use_case.execute(StopConversationInput(conv_id="conv-2c"))

    assert _stop_steps(publisher) == []
    assert result.runs_cancelled == []
    assert result.errors == []


# --- T3 --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t3_agent_publish_failure_still_dispatches_run_stops():
    """The agent.control fan-out failing is recorded but the run half still
    dispatches a workflow_stop step per run."""
    publisher = FakePublisher(fail_control=True)
    shared_state = FakeSharedStateRepo(current_run_id="run-1")
    run_index = FakeRunIndex({"conv-3": {"run-2"}})
    use_case = _build_use_case(
        publisher,
        orchestration_state_repo=shared_state,
        conversation_run_index=run_index,
    )

    result = await use_case.execute(StopConversationInput(conv_id="conv-3"))

    assert result.agent_cancelled is False
    assert any("agent cancel failed" in e for e in result.errors)
    # run stops are still dispatched despite the agent publish failing
    assert sorted(result.runs_cancelled) == ["run-1", "run-2"]
    assert sorted(_stopped_run_ids(publisher)) == ["run-1", "run-2"]


@pytest.mark.asyncio
async def test_t3_one_run_dispatch_failure_does_not_stop_others():
    publisher = FakePublisher(fail_run_ids=["run-2"])
    shared_state = FakeSharedStateRepo(current_run_id="run-1")
    run_index = FakeRunIndex({"conv-4": {"run-2", "run-3"}})
    use_case = _build_use_case(
        publisher,
        orchestration_state_repo=shared_state,
        conversation_run_index=run_index,
    )

    result = await use_case.execute(StopConversationInput(conv_id="conv-4"))

    assert result.agent_cancelled is True
    assert sorted(result.runs_cancelled) == ["run-1", "run-3"]
    assert any("run run-2 stop dispatch failed" in e for e in result.errors)


# --- N10 (union of shared-state current_run_id + in-memory index) ----------

@pytest.mark.asyncio
async def test_n10_index_dispatches_run_stops_when_no_shared_state_repo():
    """No shared-state repo wired, but the orchestrator index holds 2 run ids
    -> a workflow_stop step is dispatched for both (secondary source alone)."""
    publisher = FakePublisher()
    run_index = FakeRunIndex({"conv-10": {"run-a", "run-b"}})
    use_case = _build_use_case(publisher, conversation_run_index=run_index)

    result = await use_case.execute(StopConversationInput(conv_id="conv-10"))

    assert sorted(_stopped_run_ids(publisher)) == ["run-a", "run-b"]
    assert sorted(result.runs_cancelled) == ["run-a", "run-b"]
    assert result.errors == []


@pytest.mark.asyncio
async def test_n10_unions_shared_state_and_index_without_duplicates():
    """A run present in BOTH the shared-state current_run_id and the index gets a
    single stop step; index-only runs are added."""
    publisher = FakePublisher()
    shared_state = FakeSharedStateRepo(current_run_id="run-1")
    run_index = FakeRunIndex({"conv-11": {"run-1", "run-2"}})
    use_case = _build_use_case(
        publisher,
        orchestration_state_repo=shared_state,
        conversation_run_index=run_index,
    )

    result = await use_case.execute(StopConversationInput(conv_id="conv-11"))

    assert sorted(_stopped_run_ids(publisher)) == ["run-1", "run-2"]
    assert sorted(result.runs_cancelled) == ["run-1", "run-2"]
    assert result.errors == []


@pytest.mark.asyncio
async def test_n10_get_metadata_failure_recorded_but_index_runs_still_stopped():
    """A shared-state read failure is recorded but the index fan-out still
    dispatches a stop for the conversation's runs."""
    publisher = FakePublisher()
    shared_state = FakeSharedStateRepo(get_error=ValueError((502, "down")))
    run_index = FakeRunIndex({"conv-12": {"run-x"}})
    use_case = _build_use_case(
        publisher,
        orchestration_state_repo=shared_state,
        conversation_run_index=run_index,
    )

    result = await use_case.execute(StopConversationInput(conv_id="conv-12"))

    assert result.runs_cancelled == ["run-x"]
    assert _stopped_run_ids(publisher) == ["run-x"]
    assert any("shared-state lookup failed" in e for e in result.errors)


# --- N11 (stop observability) ----------------------------------------------

@pytest.mark.asyncio
async def test_n11_emits_metrics_and_summary_log():
    monitor = FakeMonitor()
    logger = FakeLogger()
    publisher = FakePublisher()
    shared_state = FakeSharedStateRepo(current_run_id="run-1")
    run_index = FakeRunIndex({"conv-n11": {"run-2"}})
    use_case = _build_use_case(
        publisher,
        monitor=monitor,
        conversation_run_index=run_index,
        orchestration_state_repo=shared_state,
        logger=logger,
    )

    result = await use_case.execute(StopConversationInput(conv_id="conv-n11"))

    assert result.agent_cancelled is True
    assert sorted(result.runs_cancelled) == ["run-1", "run-2"]

    totals = monitor.totals()
    assert totals["orchestrate.stop.requested"] == 1
    assert totals["orchestrate.stop.agent_cancelled"] == 1
    assert totals["orchestrate.stop.runs_cancelled"] == 2
    assert "orchestrate.stop.errors" not in totals  # no errors -> not emitted

    summary = [kw for msg, kw in logger.infos if msg == "orchestrate.stop.summary"]
    assert len(summary) == 1
    assert summary[0]["conv_id"] == "conv-n11"
    assert summary[0]["agent_cancelled"] is True
    assert summary[0]["runs_cancelled"] == 2
    assert summary[0]["errors"] == 0


@pytest.mark.asyncio
async def test_n11_error_counter_counts_failures():
    monitor = FakeMonitor()
    # Everything fails: 2 agent-control errors (business + executor topics) + 1
    # workflow_stop step-dispatch error = 3.
    publisher = FakePublisher(fail=True)
    shared_state = FakeSharedStateRepo(current_run_id="run-1")
    use_case = _build_use_case(
        publisher,
        monitor=monitor,
        orchestration_state_repo=shared_state,
    )

    result = await use_case.execute(StopConversationInput(conv_id="conv-err"))

    assert result.agent_cancelled is False
    assert result.runs_cancelled == []
    totals = monitor.totals()
    assert totals["orchestrate.stop.requested"] == 1
    assert "orchestrate.stop.agent_cancelled" not in totals
    assert "orchestrate.stop.runs_cancelled" not in totals
    # SA-892 (N4): the agent cancel fans out to 2 topics, so a failing publisher
    # yields 2 agent errors; the failing stop-step dispatch adds a 3rd.
    assert totals["orchestrate.stop.errors"] == len(result.errors) == 3


# --- L2 (tenant authorization) ---------------------------------------------

@pytest.mark.asyncio
async def test_l2_same_tenant_caller_proceeds():
    """The caller's tenant matches the conversation's tenant -> stop proceeds."""
    publisher = FakePublisher()
    shared_state = FakeSharedStateRepo(current_run_id="run-1", conv_tenant="tenant-1")
    use_case = _build_use_case(publisher, orchestration_state_repo=shared_state)

    result = await use_case.execute(
        StopConversationInput(conv_id="conv-l2a", caller_tenant_id="tenant-1")
    )

    assert result.forbidden is False
    assert result.agent_cancelled is True
    assert result.runs_cancelled == ["run-1"]
    assert _stopped_run_ids(publisher) == ["run-1"]
    assert not any("forbidden" in e for e in result.errors)


@pytest.mark.asyncio
async def test_l2_cross_tenant_caller_refused_cancels_nothing():
    """A caller from a DIFFERENT tenant is refused: nothing is cancelled, no
    agent.control cancel and no workflow_stop step is published (TENANT_BLEED)."""
    publisher = FakePublisher()
    shared_state = FakeSharedStateRepo(current_run_id="run-1", conv_tenant="tenant-A")
    use_case = _build_use_case(publisher, orchestration_state_repo=shared_state)

    result = await use_case.execute(
        StopConversationInput(conv_id="conv-l2b", caller_tenant_id="tenant-B")
    )

    assert result.forbidden is True
    assert any("forbidden" in e for e in result.errors)
    # Nothing was published at all: no control cancel, no stop step.
    assert result.agent_cancelled is False
    assert result.runs_cancelled == []
    assert publisher.calls == []
    assert shared_state.update_metadata_calls == []


@pytest.mark.asyncio
async def test_l2_no_tenant_recorded_proceeds_and_logs_caller():
    """No tenant recorded on the conversation -> proceed best-effort, but the
    caller identity is logged (audit trail for an unverified-ownership stop)."""
    logger = FakeLogger()
    publisher = FakePublisher()
    shared_state = FakeSharedStateRepo(current_run_id="run-1", conv_tenant=None)
    use_case = _build_use_case(
        publisher, orchestration_state_repo=shared_state, logger=logger
    )

    result = await use_case.execute(
        StopConversationInput(
            conv_id="conv-l2c",
            caller_tenant_id="tenant-1",
            caller_user_id="user-9",
            caller_email="u9@example.com",
        )
    )

    assert result.forbidden is False
    assert result.agent_cancelled is True
    assert result.runs_cancelled == ["run-1"]
    unverified = [kw for msg, kw in logger.infos if msg == "orchestrate.stop.tenant_unverified"]
    assert len(unverified) == 1
    assert unverified[0]["conv_id"] == "conv-l2c"
    assert unverified[0]["caller_user_id"] == "user-9"


@pytest.mark.asyncio
async def test_l2_default_sentinel_caller_tenant_is_not_refused():
    """SA-892 (review, Felix): a bearer token without tenant_id yields the
    controller's "default" sentinel. It must NOT 403 the conversation owner's own
    Stop even when the conversation carries a REAL tenant — "default" is treated as
    unknown, so the stop proceeds."""
    publisher = FakePublisher()
    shared_state = FakeSharedStateRepo(current_run_id="run-1", conv_tenant="tenant-A")
    use_case = _build_use_case(publisher, orchestration_state_repo=shared_state)

    result = await use_case.execute(
        StopConversationInput(conv_id="conv-l2d", caller_tenant_id="default")
    )

    assert result.forbidden is False
    assert result.runs_cancelled == ["run-1"]
    assert _stopped_run_ids(publisher) == ["run-1"]


@pytest.mark.asyncio
async def test_l2_default_sentinel_conversation_tenant_is_not_refused():
    """Symmetric: a conversation persisted under the "default" sentinel (created
    from a token without tenant_id) is not refused for a real-tenant caller."""
    publisher = FakePublisher()
    shared_state = FakeSharedStateRepo(current_run_id="run-1", conv_tenant="default")
    use_case = _build_use_case(publisher, orchestration_state_repo=shared_state)

    result = await use_case.execute(
        StopConversationInput(conv_id="conv-l2e", caller_tenant_id="tenant-B")
    )

    assert result.forbidden is False
    assert _stopped_run_ids(publisher) == ["run-1"]


# --- T4 (route) ------------------------------------------------------------

# Load the local orchestrator main.py explicitly (mirrors test_orchestrate_routes).
_spec = importlib.util.spec_from_file_location(
    "main",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "main.py")),
)
_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_main)
create_app = _main.create_app


class StubStopUseCase:
    def __init__(self):
        self.last_request = None

    async def execute(self, request):
        self.last_request = request
        from app.layer2_application.features.orchestrate.use_cases.stop_conversation_use_case import (
            StopConversationOutput,
        )
        return StopConversationOutput(
            conv_id=request.conv_id,
            agent_cancelled=True,
            runs_cancelled=["run-1", "run-2"],
            tasks_cancelled=["t1"],
            errors=[],
        )


def _make_app():
    _main.build_app_container = lambda: {}
    app = create_app()
    app.dependency_overrides[require_bearer_token] = lambda: {
        "user_uuid": "user-1",
        "email": "user@example.com",
        "tenant_id": "tenant-1",
    }
    stop_usecase = StubStopUseCase()
    app.state.container = {"_usecases": {"orchestrate.stop_conversation": stop_usecase}}
    return app, stop_usecase


@pytest.mark.asyncio
async def test_t4_stop_route_returns_202_contract_json():
    app, stop_usecase = _make_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/orchestrate/stop",
            json={"conv_id": "conv-42"},
            headers={"authorization": "Bearer token-abc"},
        )

    assert response.status_code == 202
    assert response.json() == {
        "conv_id": "conv-42",
        "agent_cancelled": True,
        "runs_cancelled": ["run-1", "run-2"],
        "tasks_cancelled": ["t1"],
        "errors": [],
        "forbidden": False,
    }
    assert stop_usecase.last_request.conv_id == "conv-42"
    # SA-892 (L2): the route forwards the caller's identity from the token.
    assert stop_usecase.last_request.caller_tenant_id == "tenant-1"
    assert stop_usecase.last_request.caller_user_id == "user-1"
    assert stop_usecase.last_request.caller_email == "user@example.com"


class StubForbiddenStopUseCase:
    """Returns a refused (forbidden) output so the route's 403 path is exercised."""

    async def execute(self, request):
        from app.layer2_application.features.orchestrate.use_cases.stop_conversation_use_case import (
            StopConversationOutput,
        )
        return StopConversationOutput(
            conv_id=request.conv_id,
            agent_cancelled=False,
            runs_cancelled=[],
            errors=["forbidden: conversation belongs to another tenant"],
            forbidden=True,
        )


@pytest.mark.asyncio
async def test_l2_stop_route_returns_403_when_forbidden():
    """SA-892 (L2): a cross-tenant stop surfaces as HTTP 403 at the route."""
    _main.build_app_container = lambda: {}
    app = create_app()
    app.dependency_overrides[require_bearer_token] = lambda: {
        "user_uuid": "user-1",
        "email": "user@example.com",
        "tenant_id": "tenant-OTHER",
    }
    app.state.container = {
        "_usecases": {"orchestrate.stop_conversation": StubForbiddenStopUseCase()}
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/orchestrate/stop",
            json={"conv_id": "conv-forbidden"},
            headers={"authorization": "Bearer token-abc"},
        )

    assert response.status_code == 403
    assert "another tenant" in response.json()["detail"]

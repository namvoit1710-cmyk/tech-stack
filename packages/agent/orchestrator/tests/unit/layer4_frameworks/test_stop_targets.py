"""SA-892 — unit tests for the stop-target adapters (Ports & Adapters).

Each adapter is exercised in isolation against small fakes: TaskCancel (scoping via
conversation_context + fallback), AgentControl (config-driven fan-out incl. B2
``AGENT_CONTROL_TOPICS`` + dedupe + best-effort), WorkflowStop (run resolution +
dispatch + unresolved-skip). Every adapter is best-effort: an expected failure is
recorded in the result, never raised.
"""

from types import SimpleNamespace

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    GlobalContext,
    Task,
    TaskStatus,
)
from agent_sdk.layer1_domain.entities.conversation_context import (
    MessageHistory,
    TaskHistory,
)
from app.layer2_application.interfaces.stop_target import StopContext
from app.layer4_frameworks.stop_targets.agent_control_stop_target import (
    AgentControlStopTarget,
)
from app.layer4_frameworks.stop_targets.task_cancel_stop_target import (
    TaskCancelStopTarget,
)
from app.layer4_frameworks.stop_targets.workflow_stop_target import (
    WorkflowStopTarget,
)


class _Logger:
    def info(self, *a, **k):
        pass

    warning = error = debug = info


class _Publisher:
    def __init__(self, fail_topics=None):
        self.published = []  # (topic, message, key)
        self._fail = set(fail_topics or [])

    async def publish(self, topic, message=None, key=None):
        if topic in self._fail:
            raise RuntimeError(f"publish failed: {topic}")
        self.published.append((topic, message, key))

    @property
    def topics(self):
        return [t for t, _m, _k in self.published]


def _task(task_id, status):
    return Task(task_id=task_id, agent="agent.file", name=f"t {task_id}", status=status)


def _conversation(current_message_id, executions, context_map):
    context = GlobalContext(
        conversation_context=[
            MessageHistory(
                message_id=mid,
                message=f"m {mid}",
                tasks=[TaskHistory(task_id=t, knowledge=[]) for t in tids],
            )
            for mid, tids in context_map.items()
        ]
    )
    shared_state = SimpleNamespace(
        metadata=SimpleNamespace(current_message_id=current_message_id),
        executions=executions,
        context=context,
    )
    return SimpleNamespace(shared_state=shared_state)


class _TaskRepo:
    def __init__(self, conversation=None, update_error=None):
        self._conversation = conversation
        self._update_error = update_error
        self.update_task_calls = []  # (conv_id, task_id, kwargs)

    def get_conversation(self, conv_id):
        return self._conversation

    def update_task(self, conv_id, task_id, **kwargs):
        if self._update_error is not None:
            raise self._update_error
        self.update_task_calls.append((conv_id, task_id, kwargs))


_CTX = StopContext(conv_id="conv-1")


# ── TaskCancelStopTarget ───────────────────────────────────────────────────


async def test_task_cancel_scopes_to_current_message_via_context():
    executions = [
        _task("t1", TaskStatus.PROCESSING),  # current msg, non-terminal
        _task("t2", TaskStatus.PENDING),      # current msg, non-terminal
        _task("t3", TaskStatus.SUCCESS),      # current msg, TERMINAL -> skip
        _task("t4", TaskStatus.PROCESSING),   # DIFFERENT msg -> not in scope
    ]
    conv = _conversation(
        "msg-A", executions, {"msg-A": ["t1", "t2", "t3"], "msg-B": ["t4"]}
    )
    repo = _TaskRepo(conversation=conv)
    result = await TaskCancelStopTarget(repo, _Logger()).stop(_CTX)

    assert sorted(result.tasks_cancelled) == ["t1", "t2"]
    assert sorted(tid for _c, tid, _k in repo.update_task_calls) == ["t1", "t2"]
    assert all(
        kw.get("status") == TaskStatus.CANCELLED for _c, _t, kw in repo.update_task_calls
    )
    assert result.errors == []


async def test_task_cancel_falls_back_to_all_non_terminal_when_unresolved():
    executions = [
        _task("t1", TaskStatus.PROCESSING),
        _task("t2", TaskStatus.FAIL),  # terminal -> skip
        _task("t3", TaskStatus.PENDING),
    ]
    # current message absent from conversation_context -> fallback
    conv = _conversation("msg-CURRENT", executions, {"msg-OTHER": ["t9"]})
    result = await TaskCancelStopTarget(_TaskRepo(conversation=conv), _Logger()).stop(_CTX)
    assert sorted(result.tasks_cancelled) == ["t1", "t3"]


async def test_task_cancel_present_but_empty_cancels_nothing():
    executions = [_task("t9", TaskStatus.PROCESSING)]
    # current message present but registered NO tasks -> scope empty -> nothing
    conv = _conversation("msg-A", executions, {"msg-A": [], "msg-B": ["t9"]})
    result = await TaskCancelStopTarget(_TaskRepo(conversation=conv), _Logger()).stop(_CTX)
    assert result.tasks_cancelled == []


async def test_task_cancel_no_repo_is_noop():
    result = await TaskCancelStopTarget(None, _Logger()).stop(_CTX)
    assert result.tasks_cancelled == [] and result.errors == []


async def test_task_cancel_lookup_error_recorded_not_raised():
    class _Boom:
        def get_conversation(self, conv_id):
            raise RuntimeError("db down")

    result = await TaskCancelStopTarget(_Boom(), _Logger()).stop(_CTX)
    assert result.tasks_cancelled == []
    assert any("cancel tasks lookup failed" in e for e in result.errors)


# ── AgentControlStopTarget ─────────────────────────────────────────────────


async def test_agent_control_b2_config_list_string_drives_topics():
    # B2: an explicit comma-separated AGENT_CONTROL_TOPICS fans out to each topic.
    settings = SimpleNamespace(AGENT_CONTROL_TOPICS="a.topic, b.topic , c.topic")
    pub = _Publisher()
    result = await AgentControlStopTarget(pub, settings, _Logger()).stop(_CTX)
    assert pub.topics == ["a.topic", "b.topic", "c.topic"]
    assert result.agent_cancelled is True
    assert all(k == "conv-1" for _t, _m, k in pub.published)


async def test_agent_control_b2_config_list_iterable_drives_topics():
    settings = SimpleNamespace(AGENT_CONTROL_TOPICS=["x.topic", "y.topic"])
    pub = _Publisher()
    await AgentControlStopTarget(pub, settings, _Logger()).stop(_CTX)
    assert pub.topics == ["x.topic", "y.topic"]


async def test_agent_control_falls_back_to_business_and_executor_pair():
    settings = SimpleNamespace(
        AGENT_REQUEST_TOPIC="business.topic",
        AGENT_EXECUTOR_CONTROL_TOPIC="agent.request.executor",
    )
    pub = _Publisher()
    await AgentControlStopTarget(pub, settings, _Logger()).stop(_CTX)
    assert pub.topics == ["business.topic", "agent.request.executor"]


async def test_agent_control_dedupes_identical_topics():
    # business == executor -> published once.
    settings = SimpleNamespace(
        AGENT_REQUEST_TOPIC="shared.topic",
        AGENT_EXECUTOR_CONTROL_TOPIC="shared.topic",
    )
    pub = _Publisher()
    await AgentControlStopTarget(pub, settings, _Logger()).stop(_CTX)
    assert pub.topics == ["shared.topic"]


async def test_agent_control_best_effort_one_topic_fails():
    settings = SimpleNamespace(AGENT_CONTROL_TOPICS="a.topic,b.topic")
    pub = _Publisher(fail_topics=["a.topic"])
    result = await AgentControlStopTarget(pub, settings, _Logger()).stop(_CTX)
    assert pub.topics == ["b.topic"]  # second still published
    assert result.agent_cancelled is True
    assert any("a.topic" in e for e in result.errors)


# ── WorkflowStopTarget ─────────────────────────────────────────────────────


class _MetaRepo:
    def __init__(self, current_run_id=""):
        self._run = current_run_id

    def get_metadata(self, conv_id):
        return SimpleNamespace(current_run_id=self._run)


class _Index:
    def __init__(self, ids=None):
        self._ids = set(ids or [])

    def get_run_ids(self, conv_id):
        return set(self._ids)


class _Registry:
    def __init__(self, ids=None):
        self._ids = ids or {}
        self.lookups = []

    async def get_tool_id_by_name(self, name):
        self.lookups.append(name)
        return self._ids.get(name)


def _wf_settings(**over):
    base = {"EXECUTOR_TOPIC": "executor.topic.test"}
    base.update(over)
    return SimpleNamespace(**base)


async def test_workflow_stop_dispatches_per_resolved_run_with_override():
    settings = _wf_settings(WORKFLOW_STOP_EXECUTE_BY="tool.override-1")
    pub = _Publisher()
    target = WorkflowStopTarget(
        publisher=pub,
        settings=settings,
        logger=_Logger(),
        orchestration_state_repo=_MetaRepo(current_run_id="run-A"),
        conversation_run_index=_Index(["run-B"]),
    )
    result = await target.stop(_CTX)
    assert sorted(result.runs_cancelled) == ["run-A", "run-B"]
    # each dispatch carries the run_id + the resolved execute_by
    run_ids = {
        m["payload"]["steps"][0]["input_data"]["run_id"] for _t, m, _k in pub.published
    }
    execute_bys = {
        m["payload"]["steps"][0]["execute_by"] for _t, m, _k in pub.published
    }
    assert run_ids == {"run-A", "run-B"}
    assert execute_bys == {"tool.override-1"}
    assert pub.topics == ["executor.topic.test", "executor.topic.test"]


async def test_workflow_stop_discovers_execute_by_from_registry_by_name():
    # No override -> resolve the workflow_stop tool id BY NAME -> tool.<id>.
    registry = _Registry(ids={"workflow_stop": "abc-123"})
    pub = _Publisher()
    target = WorkflowStopTarget(
        publisher=pub,
        settings=_wf_settings(),
        logger=_Logger(),
        agent_registry_client=registry,
        orchestration_state_repo=_MetaRepo(current_run_id="run-A"),
    )
    result = await target.stop(_CTX)
    assert registry.lookups == ["workflow_stop"]
    assert result.runs_cancelled == ["run-A"]
    assert pub.published[0][1]["payload"]["steps"][0]["execute_by"] == "tool.abc-123"


async def test_workflow_stop_skips_and_records_when_execute_by_unresolved():
    # runs present but no override + registry returns nothing -> skip, record why.
    registry = _Registry(ids={})
    pub = _Publisher()
    target = WorkflowStopTarget(
        publisher=pub,
        settings=_wf_settings(),
        logger=_Logger(),
        agent_registry_client=registry,
        orchestration_state_repo=_MetaRepo(current_run_id="run-A"),
    )
    result = await target.stop(_CTX)
    assert result.runs_cancelled == []
    assert pub.published == []
    assert any("workflow-stop capability unresolved" in e for e in result.errors)


async def test_workflow_stop_best_effort_one_dispatch_fails():
    settings = _wf_settings(WORKFLOW_STOP_EXECUTE_BY="tool.override-1")
    pub = _Publisher(fail_topics=["executor.topic.test"])  # every publish fails
    target = WorkflowStopTarget(
        publisher=pub,
        settings=settings,
        logger=_Logger(),
        orchestration_state_repo=_MetaRepo(current_run_id="run-A"),
    )
    result = await target.stop(_CTX)
    assert result.runs_cancelled == []
    assert any("run run-A stop dispatch failed" in e for e in result.errors)

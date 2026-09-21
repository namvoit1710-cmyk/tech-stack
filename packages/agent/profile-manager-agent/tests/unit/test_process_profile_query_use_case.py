"""Unit specs for ProcessProfileQueryUseCase (layer2 — the orchestration core).

Target: app/layer2_application/features/execute_agent/use_cases/process_profile_query_use_case.py

Replaces the old test_profile_manager_processor.py node-orchestration cases.
Collaborators arrive as constructor-injected ports (DIP, P2):
- planner: IPlannerPort  -> .plan is async
- user_query: IUserQueryPort -> .query is async
Every expected `formatted_response` value is grounded in the source.
"""
import pytest

from app.layer1_domain.entities.execution_plan import ExecutionPlan, PlanStep
from app.layer1_domain.entities.odata_query import ODataQuery
from app.layer1_domain.entities.query_result import QueryResult
from app.layer2_application.features.execute_agent.use_cases.process_profile_query_use_case import (
    ProcessProfileQueryCommand,
    ProcessProfileQueryUseCase,
)

AGENT_TYPE = "profile-manager"


class FakePlanner:
    def __init__(self, plan=None, error=None):
        self._plan = plan
        self._error = error
        self.calls = []

    async def plan(self, user_prompt: str) -> ExecutionPlan:
        self.calls.append(user_prompt)
        if self._error:
            raise self._error
        return self._plan


class FakeUserQuery:
    def __init__(self, result=None, error=None):
        self._result = result if result is not None else QueryResult()
        self._error = error
        self.calls = []

    async def query(self, steps, token):
        self.calls.append((steps, token))
        if self._error:
            raise self._error
        return self._result


def _ok_plan(scope="user"):
    return ExecutionPlan(
        status="ok",
        steps=[PlanStep(scope=scope, odata_query=ODataQuery(filter="x eq 'y'"))],
        user_prompt="find y",
    )


def _cmd(message="find y", token="tok-123"):
    return ProcessProfileQueryCommand(message=message, token=token, agent_type=AGENT_TYPE)


# happy path -> success response, port awaited with prompt + token threaded
@pytest.mark.asyncio
async def test_happy_path_returns_success_response():
    planner = FakePlanner(plan=_ok_plan())
    user_query = FakeUserQuery(result=QueryResult(users=[{"id": 1}], odata_query="q"))
    uc = ProcessProfileQueryUseCase(planner=planner, user_query=user_query)

    result = await uc.execute(_cmd())
    fr = result.as_dict()

    assert fr["status"] == "success"
    assert fr["content"] == "Profile Manager Agent execute successfully."
    assert fr["agent_type"] == AGENT_TYPE
    assert fr["transport_state"] == "RECEIVED"
    assert fr["agent_data"]["valid"] == "correct"
    assert fr["agent_data"]["agent_type"] == AGENT_TYPE
    assert fr["agent_data"]["item_size"] == "1"
    # planner awaited with the message; token threaded to the user-query port
    assert planner.calls == ["find y"]
    assert user_query.calls[0][1] == "tok-123"


# planner "not_supported" -> not-support response, executor never called
@pytest.mark.asyncio
async def test_not_supported_status_returns_not_support_response():
    planner = FakePlanner(plan=ExecutionPlan(status="not_supported"))
    user_query = FakeUserQuery()
    uc = ProcessProfileQueryUseCase(planner=planner, user_query=user_query)

    fr = (await uc.execute(_cmd(message="do impossible"))).as_dict()

    assert fr["status"] == "error"
    assert fr["content"] == "Profile Manager Agent not supported."
    assert fr["agent_data"]["message"] == "Profile Manager Agent not supported."
    assert "transport_state" not in fr
    assert user_query.calls == []


# planner "error" takes the same not-support branch
@pytest.mark.asyncio
async def test_error_status_returns_not_support_response():
    planner = FakePlanner(plan=ExecutionPlan(status="error"))
    uc = ProcessProfileQueryUseCase(planner=planner, user_query=FakeUserQuery())

    fr = (await uc.execute(_cmd(message="boom"))).as_dict()
    assert fr["content"] == "Profile Manager Agent not supported."


# planner raises -> caught -> error response carries str(e)
@pytest.mark.asyncio
async def test_planner_raises_returns_error_response():
    planner = FakePlanner(error=RuntimeError("llm down"))
    uc = ProcessProfileQueryUseCase(planner=planner, user_query=FakeUserQuery())

    fr = (await uc.execute(_cmd())).as_dict()

    assert fr["status"] == "error"
    assert fr["content"] == "Profile Manager Agent execute fail."
    assert fr["agent_data"]["valid"] == "incorrect"
    assert "llm down" in fr["agent_data"]["message"]
    assert "transport_state" not in fr


# user-query port raises -> caught -> error response
@pytest.mark.asyncio
async def test_user_query_raises_returns_error_response():
    planner = FakePlanner(plan=_ok_plan())
    user_query = FakeUserQuery(error=RuntimeError("svc down"))
    uc = ProcessProfileQueryUseCase(planner=planner, user_query=user_query)

    fr = (await uc.execute(_cmd())).as_dict()
    assert fr["status"] == "error"
    assert "svc down" in fr["agent_data"]["message"]


# unsupported scope -> ValueError -> caught -> error response (preserves old guard)
@pytest.mark.asyncio
async def test_unsupported_scope_returns_error_response():
    planner = FakePlanner(plan=_ok_plan(scope="system"))
    uc = ProcessProfileQueryUseCase(planner=planner, user_query=FakeUserQuery())

    fr = (await uc.execute(_cmd())).as_dict()
    assert fr["status"] == "error"
    assert "Unsupported scope: system" in fr["agent_data"]["message"]


# empty plan (ok status, no steps) -> empty result -> warning (defined behaviour)
@pytest.mark.asyncio
async def test_empty_plan_returns_warning_success():
    planner = FakePlanner(plan=ExecutionPlan(status="ok", steps=[], user_prompt="p"))
    uc = ProcessProfileQueryUseCase(planner=planner, user_query=FakeUserQuery())

    fr = (await uc.execute(_cmd())).as_dict()
    assert fr["status"] == "success"
    assert fr["agent_data"]["valid"] == "warning"
    assert fr["agent_data"]["item_size"] == "0"

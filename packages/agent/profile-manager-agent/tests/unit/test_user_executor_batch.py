"""Unit specs for UserQueryRepository.query (layer4 odata_services, async).

Target: app/layer4_frameworks/odata_services/user_executor.py

Grounded behaviour (async migration of the old execute_batch, P9):
- builds one ODataQuery per PlanStep and calls _execute_single;
- returns the LAST step's QueryResult;
- token is threaded to _execute_single;
- empty step list -> empty QueryResult (was UnboundLocalError; now defined).
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.layer1_domain.entities.execution_plan import PlanStep
from app.layer1_domain.entities.odata_query import ODataQuery
from app.layer1_domain.entities.query_result import QueryResult
from app.layer4_frameworks.odata_services.user_executor import UserQueryRepository


@pytest.fixture
def repo():
    with patch(
        "app.layer4_frameworks.odata_services.user_executor.AppSettingsAdapter"
    ) as mock_settings:
        mock_settings.return_value.PROFILE_MANAGEMENT_BASE_URL = "https://svc.example"
        yield UserQueryRepository()


def _step(**odata):
    return PlanStep(scope="user", odata_query=ODataQuery(**odata))


# happy path: single step -> _execute_single called with the built ODataQuery + token
@pytest.mark.asyncio
async def test_query_single_step(repo):
    repo._execute_single = AsyncMock(return_value=QueryResult(users=[{"id": 1}]))

    result = await repo.query([_step(filter="name eq 'A'")], token="tk")

    assert result.users == [{"id": 1}]
    called_query, called_token = repo._execute_single.call_args[0]
    assert called_query.filter == "name eq 'A'"
    assert called_token == "tk"


# edge: multiple steps -> returns the LAST result
@pytest.mark.asyncio
async def test_query_returns_last_step_result(repo):
    repo._execute_single = AsyncMock(
        side_effect=[QueryResult(users=["first"]), QueryResult(users=["second"])]
    )

    result = await repo.query([_step(filter="a eq 1"), _step(filter="b eq 2")], token=None)

    assert result.users == ["second"]
    assert repo._execute_single.call_count == 2


# boundary: empty step list -> empty QueryResult (defined behaviour, no crash)
@pytest.mark.asyncio
async def test_query_empty_steps_returns_empty_result(repo):
    result = await repo.query([], token="tk")
    assert isinstance(result, QueryResult)
    assert result.users == []


# failure path: _execute_single raises -> propagates out of the batch
@pytest.mark.asyncio
async def test_query_propagates_single_error(repo):
    repo._execute_single = AsyncMock(side_effect=RuntimeError("svc down"))
    with pytest.raises(RuntimeError, match="svc down"):
        await repo.query([_step(filter="x eq 1")], token="tk")

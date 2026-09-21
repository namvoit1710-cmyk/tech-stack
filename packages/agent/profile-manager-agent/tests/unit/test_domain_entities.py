"""Unit specs for the layer1 domain entities added/extended by SA-1442.

Targets:
- app/layer1_domain/entities/odata_query.py (ODataQuery dataclass + create())
- app/layer1_domain/entities/execution_plan.py (ExecutionPlan / PlanStep)
- app/layer1_domain/entities/query_result.py (QueryResult / QueryEvaluation)
"""
import pytest

from app.layer1_domain.entities.odata_query import ODataQuery
from app.layer1_domain.entities.execution_plan import ExecutionPlan, PlanStep


# --------------------------------------------------------------------------- #
# ODataQuery — dataclass construction stays backward compatible
# --------------------------------------------------------------------------- #

def test_odata_query_from_dict_still_works():
    q = ODataQuery(**{"filter": "x eq 1", "top": 3})
    assert q.filter == "x eq 1"
    assert q.top == 3
    assert q.skip is None


def test_odata_query_defaults_all_none():
    q = ODataQuery()
    assert (q.filter, q.orderby, q.top, q.skip, q.select, q.count) == (
        None, None, None, None, None, None,
    )


# create() adds validation (does not affect plain construction)
def test_odata_query_create_happy_path():
    q = ODataQuery.create(filter="name eq 'A'", top=10, skip=0)
    assert q.filter == "name eq 'A'"
    assert q.top == 10


@pytest.mark.parametrize("bad", [{"top": -1}, {"skip": -5}, {"filter": "   "}])
def test_odata_query_create_rejects_invalid(bad):
    with pytest.raises(ValueError):
        ODataQuery.create(**bad)


# --------------------------------------------------------------------------- #
# ExecutionPlan.from_planner_dict — parses the LLM JSON shape
# --------------------------------------------------------------------------- #

def test_execution_plan_from_planner_dict_builds_steps():
    plan = ExecutionPlan.from_planner_dict(
        {
            "status": "ok",
            "execution_plan": [
                {"scope": "user", "odata_query": {"filter": "name eq 'A'"}}
            ],
            "user_prompt": "find A",
        }
    )
    assert plan.status == "ok"
    assert plan.is_supported is True
    assert plan.user_prompt == "find A"
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert isinstance(step, PlanStep)
    assert step.scope == "user"
    assert isinstance(step.odata_query, ODataQuery)
    assert step.odata_query.filter == "name eq 'A'"


@pytest.mark.parametrize("status", ["not_supported", "error"])
def test_execution_plan_unsupported_statuses(status):
    plan = ExecutionPlan.from_planner_dict({"status": status, "execution_plan": []})
    assert plan.is_supported is False
    assert plan.steps == []


def test_execution_plan_missing_odata_query_defaults_empty():
    plan = ExecutionPlan.from_planner_dict(
        {"status": "ok", "execution_plan": [{"scope": "user"}]}
    )
    assert plan.steps[0].odata_query == ODataQuery()

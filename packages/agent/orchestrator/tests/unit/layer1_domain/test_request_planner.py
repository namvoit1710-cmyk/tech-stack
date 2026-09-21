from unittest.mock import MagicMock

from app.layer1_domain.entities.planner.request_planner import RequestPlanner
from app.layer1_domain.entities.planner.task_breakdown_response import (
    ParsedSharedStateTaskModel,
    StrategicWorkflowPlan,
    TaskBreakdownResponse,
)


def test_create_strategy_requests_structured_plan_and_stores_result():
    llm_provider = MagicMock()
    logger = MagicMock()
    strategy = StrategicWorkflowPlan(
        context_summary="Validate file then create request",
        logical_steps=["Validate file", "Create change request"],
        dependency_rules="Second step depends on the first.",
    )
    llm_provider.complete_with_format.return_value = strategy

    planner = RequestPlanner()

    result = planner.create_strategy(
        strategy_prompt=[{"role": "user", "content": "plan this"}],
        llm_provider=llm_provider,
        logger=logger,
    )

    assert result == strategy
    assert planner.strategy == strategy
    llm_provider.complete_with_format.assert_called_once_with(
        messages=[{"role": "user", "content": "plan this"}],
        response_model=StrategicWorkflowPlan,
    )


def test_create_plan_from_strategy_materializes_sequential_tasks():
    llm_provider = MagicMock()
    logger = MagicMock()
    strategy = StrategicWorkflowPlan(
        context_summary="Validate file then create request",
        logical_steps=["Validate file", "Create change request"],
        dependency_rules="Second step depends on the first.",
    )
    llm_provider.complete_with_format.side_effect = [
        strategy,
        TaskBreakdownResponse(
            reasoning="Second step depends on the first.",
            tasks=[
                ParsedSharedStateTaskModel(
                    goal="Check uploaded file",
                    input_schema=["uploaded_file"],
                    output_schema=["validated_file"],
                ),
                ParsedSharedStateTaskModel(
                    goal="Create the change request",
                    input_schema=["validated_file"],
                    output_schema=["change_request"],
                ),
            ],
        ),
    ]
    planner = RequestPlanner()

    planner.create_strategy(
        strategy_prompt=[{"role": "user", "content": "plan this"}],
        llm_provider=llm_provider,
        logger=logger,
    )
    plan = planner.create_plan_from_strategy(
        message="check the uploaded file and then create the change request",
        plan_prompt=[{"role": "user", "content": "break this down"}],
        llm_provider=llm_provider,
        logger=logger,
    )

    assert llm_provider.complete_with_format.call_count == 2
    assert len(plan.tasks) == 2
    assert plan.tasks[0].goal == "Check uploaded file"
    assert plan.tasks[0].depend_on == []
    assert plan.tasks[1].depend_on == []
    assert plan.reasoning == "Second step depends on the first."


def test_create_plan_from_strategy_returns_empty_plan_when_strategy_missing():
    llm_provider = MagicMock()
    logger = MagicMock()
    planner = RequestPlanner()
    planner.__dict__["strategy"] = None

    plan = planner.create_plan_from_strategy(
        message="review the file and then create the change request",
        plan_prompt=[{"role": "user", "content": "break this down"}],
        llm_provider=llm_provider,
        logger=logger,
    )

    assert plan.tasks == []
    assert plan.reasoning == "No strategy available to create plan."
    llm_provider.complete_with_format.assert_not_called()


def test_create_plan_from_strategy_returns_default_plan_when_llm_fails():
    llm_provider = MagicMock()
    logger = MagicMock()
    planner = RequestPlanner()
    planner.strategy = StrategicWorkflowPlan(
        context_summary="Many steps",
        logical_steps=["One", "Two", "Three"],
        dependency_rules="Sequential",
    )
    llm_provider.complete_with_format.side_effect = RuntimeError("boom")

    plan = planner.create_plan_from_strategy(
        message="do three things",
        plan_prompt=[{"role": "user", "content": "break this down"}],
        llm_provider=llm_provider,
        logger=logger,
    )

    assert plan.tasks == []
    assert plan.reasoning == "Default planning: Planning failed: boom"
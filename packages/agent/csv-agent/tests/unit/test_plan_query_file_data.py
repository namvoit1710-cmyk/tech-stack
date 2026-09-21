from __future__ import annotations

import pytest

from app.layer2_application.use_cases.plan_usecase import PlanInputDto, PlanUseCase


@pytest.mark.asyncio
async def test_fallback_selects_query_file_data_for_read_only_filter_request() -> None:
    usecase = PlanUseCase()

    output = await usecase.execute(
        PlanInputDto(
            message="show rows where status = active top 5",
            file_id="file_550e8400-e29b-41d4-a716-446655440000",
            parameters={"file_id": "file_550e8400-e29b-41d4-a716-446655440000"},
            tool_catalog=[{"name": "convert_json_to_csv"}, {"name": "query_file_data"}],
        )
    )

    step = output.execution_plan.steps[0]
    assert step.tool == "query_file_data"
    assert "$filter=status eq 'active'" in step.input["odata_query"]
    assert "$top=5" in step.input["odata_query"]


@pytest.mark.asyncio
async def test_fallback_keeps_transform_column_for_new_filtered_file_request() -> None:
    usecase = PlanUseCase()

    output = await usecase.execute(
        PlanInputDto(
            message="filter status active and save a new filtered file",
            file_id="file_550e8400-e29b-41d4-a716-446655440000",
            parameters={"file_id": "file_550e8400-e29b-41d4-a716-446655440000"},
            tool_catalog=[{"name": "query_file_data"}, {"name": "transform_column"}],
        )
    )

    assert output.execution_plan.steps[0].tool == "transform_column"


class _StructuredPlanGateway:
    async def generate_execution_plan(
        self, *, system_prompt: str, user_prompt: str
    ) -> dict:
        assert "Available tools" in system_prompt
        assert "show first 2 rows" in user_prompt
        return {
            "goal": "query file",
            "steps": [
                {
                    "order": 1,
                    "tool": "query_file_data",
                    "input": {"odata_query": "$top=2"},
                    "reason": "read only",
                }
            ],
        }


@pytest.mark.asyncio
async def test_plan_usecase_uses_structured_gateway_without_legacy_llm_service() -> (
    None
):
    usecase = PlanUseCase(structured_llm_gateway=_StructuredPlanGateway())

    output = await usecase.execute(
        PlanInputDto(
            message="show first 2 rows",
            file_id="file_550e8400-e29b-41d4-a716-446655440000",
            parameters={"file_id": "file_550e8400-e29b-41d4-a716-446655440000"},
            tool_catalog=[{"name": "query_file_data"}],
        )
    )

    step = output.execution_plan.steps[0]
    assert step.tool == "query_file_data"
    assert step.input == {"odata_query": "$top=2"}


class _BrokenStructuredPlanGateway:
    async def generate_execution_plan(
        self, *, system_prompt: str, user_prompt: str
    ) -> dict:
        raise ValueError("bad structured response")


@pytest.mark.asyncio
async def test_plan_usecase_falls_back_when_structured_gateway_fails() -> None:
    usecase = PlanUseCase(structured_llm_gateway=_BrokenStructuredPlanGateway())

    output = await usecase.execute(
        PlanInputDto(
            message="convert this csv to json",
            file_id="file_550e8400-e29b-41d4-a716-446655440000",
            parameters={"file_id": "file_550e8400-e29b-41d4-a716-446655440000"},
            tool_catalog=[{"name": "convert_csv_to_json"}],
        )
    )

    assert output.execution_plan.goal == "convert this csv to json"
    assert output.execution_plan.steps[0].tool == "convert_csv_to_json"


@pytest.mark.asyncio
async def test_fallback_selects_json_to_csv_for_json_to_csv_message() -> None:
    usecase = PlanUseCase()

    output = await usecase.execute(
        PlanInputDto(
            message="convert this json to csv",
            parameters={},
            tool_catalog=[{"name": "convert_json_to_csv"}],
        )
    )

    assert output.execution_plan.steps[0].tool == "convert_json_to_csv"


@pytest.mark.asyncio
async def test_fallback_selects_csv_to_json_for_csv_to_json_message() -> None:
    usecase = PlanUseCase()

    output = await usecase.execute(
        PlanInputDto(
            message="convert this csv to json",
            file_id="file_550e8400-e29b-41d4-a716-446655440000",
            parameters={"file_id": "file_550e8400-e29b-41d4-a716-446655440000"},
            tool_catalog=[{"name": "convert_csv_to_json"}],
        )
    )

    assert output.execution_plan.steps[0].tool == "convert_csv_to_json"

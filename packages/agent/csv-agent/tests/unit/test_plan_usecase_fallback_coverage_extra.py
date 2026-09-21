from __future__ import annotations

import pytest

from app.layer2_application.use_cases.plan_usecase import PlanInputDto, PlanUseCase


@pytest.mark.parametrize(
    ("message", "expected_query"),
    [
        ("show top 3 rows", "$top=3"),
        (
            "select name, status where status = active",
            "$select=name,status&$filter=status eq 'active'",
        ),
        ("read rows order by created_at desc", "$orderby=created_at desc"),
        (
            "query ?$filter=status eq 'active'&$top=1",
            "$filter=status eq 'active'&$top=1",
        ),
    ],
)
def test_derive_odata_query_from_common_read_only_messages(
    message: str, expected_query: str
) -> None:
    assert PlanUseCase._derive_odata_query_from_message(message) == expected_query


@pytest.mark.asyncio
async def test_fallback_uses_first_available_tool_when_intent_is_unknown() -> None:
    usecase = PlanUseCase()

    output = await usecase.execute(
        PlanInputDto(
            message="please help",
            file_id="file_550e8400-e29b-41d4-a716-446655440000",
            parameters={"file_id": "file_550e8400-e29b-41d4-a716-446655440000"},
            tool_catalog=[{"name": "query_file_data"}, {"name": "transform_column"}],
        )
    )

    assert output.execution_plan.steps[0].tool == "query_file_data"


@pytest.mark.asyncio
async def test_fallback_selects_row_transform_for_row_mutations() -> None:
    usecase = PlanUseCase()

    output = await usecase.execute(
        PlanInputDto(
            message="delete row 5",
            file_id="file_550e8400-e29b-41d4-a716-446655440000",
            parameters={"file_id": "file_550e8400-e29b-41d4-a716-446655440000"},
            tool_catalog=[{"name": "transform_row"}],
        )
    )

    assert output.execution_plan.steps[0].tool == "transform_row"


@pytest.mark.asyncio
async def test_fallback_selects_column_transform_for_column_mutations() -> None:
    usecase = PlanUseCase()

    output = await usecase.execute(
        PlanInputDto(
            message="rename column old_name to new_name",
            file_id="file_550e8400-e29b-41d4-a716-446655440000",
            parameters={"file_id": "file_550e8400-e29b-41d4-a716-446655440000"},
            tool_catalog=[{"name": "transform_column"}],
        )
    )

    assert output.execution_plan.steps[0].tool == "transform_column"


@pytest.mark.asyncio
async def test_fallback_selects_json_to_csv_when_json_data_parameter_exists() -> None:
    usecase = PlanUseCase()

    output = await usecase.execute(
        PlanInputDto(
            message="convert this",
            parameters={"json_data": '[{"a": 1}]'},
            tool_catalog=[{"name": "convert_json_to_csv"}],
        )
    )

    assert output.execution_plan.steps[0].tool == "convert_json_to_csv"


@pytest.mark.asyncio
async def test_fallback_selects_query_file_data_when_odata_query_parameter_exists() -> (
    None
):
    usecase = PlanUseCase()

    output = await usecase.execute(
        PlanInputDto(
            message="run this query",
            file_id="file_550e8400-e29b-41d4-a716-446655440000",
            parameters={
                "file_id": "file_550e8400-e29b-41d4-a716-446655440000",
                "odata_query": "$top=10",
            },
            tool_catalog=[{"name": "query_file_data"}],
        )
    )

    assert output.execution_plan.steps[0].tool == "query_file_data"

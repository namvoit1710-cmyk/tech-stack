from __future__ import annotations

import pytest

from app.layer2_application.use_cases.plan_usecase import PlanInputDto, PlanUseCase


class _StructuredPlanGateway:
    def __init__(self, plan=None, error: Exception | None = None) -> None:
        self.plan = plan
        self.error = error
        self.calls: list[dict[str, str]] = []

    async def generate_execution_plan(self, *, system_prompt: str, user_prompt: str):
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if self.error is not None:
            raise self.error
        return self.plan


@pytest.mark.asyncio
async def test_plan_usecase_accepts_structured_completion_dict():
    gateway = _StructuredPlanGateway(
        {
            "goal": "structured",
            "steps": [
                {
                    "order": 1,
                    "tool": "query_file_data",
                    "input": {"odata_query": "$top=5"},
                    "reason": "structured gateway chose query tool",
                }
            ],
        }
    )
    usecase = PlanUseCase(structured_llm_gateway=gateway)

    output = await usecase.execute(PlanInputDto(message="show rows", file_id="file_1"))

    assert output.execution_plan.goal == "structured"
    assert output.execution_plan.steps[0].tool == "query_file_data"
    assert output.execution_plan.steps[0].input == {"odata_query": "$top=5"}
    assert gateway.calls


@pytest.mark.asyncio
async def test_plan_usecase_uses_structured_gateway_payload_without_json_parsing():
    gateway = _StructuredPlanGateway(
        {
            "goal": "fenced",
            "steps": [
                {
                    "order": "1",
                    "tool": "convert_csv_to_json",
                    "input": {},
                    "reason": "structured gateway already parsed/validated output",
                }
            ],
        }
    )
    usecase = PlanUseCase(structured_llm_gateway=gateway)

    output = await usecase.execute(
        PlanInputDto(message="convert csv to json", file_id="file_1")
    )

    assert output.execution_plan.goal == "fenced"
    assert output.execution_plan.steps[0].order == 1
    assert output.execution_plan.steps[0].tool == "convert_csv_to_json"


@pytest.mark.asyncio
async def test_plan_usecase_falls_back_when_structured_gateway_rejects_payload():
    usecase = PlanUseCase(
        structured_llm_gateway=_StructuredPlanGateway(error=ValueError("bad shape"))
    )

    output = await usecase.execute(
        PlanInputDto(message="show top 3 rows", file_id="file_1")
    )

    assert output.execution_plan.goal == "show top 3 rows"
    assert output.execution_plan.steps[0].tool == "query_file_data"
    assert output.execution_plan.steps[0].input == {"odata_query": "$top=3"}


@pytest.mark.asyncio
async def test_plan_usecase_rule_fallback_still_handles_json_parameter():
    usecase = PlanUseCase()

    output = await usecase.execute(
        PlanInputDto(
            message="please convert this",
            parameters={"json_data": '[{"id": 1}]'},
        )
    )

    assert output.execution_plan.steps[0].tool == "convert_json_to_csv"

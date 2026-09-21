import json

from app.layer1_domain.entities.execution_plan import ExecutionPlan
from app.layer2_application.features.execute_agent.operation_loader import (
    build_operations_prompt,
)
from app.layer2_application.features.execute_agent.prompt.transformation_planner_prompt import (
    build_plan_prompt,
)
from app.layer2_application.interfaces.llm_service_port import ILLMService
from app.layer2_application.interfaces.operation_catalog_port import IOperationCatalog


class OpenAIPlanner:
    """LLM-backed planner (implements IPlannerPort).

    The LLM (``ILLMService``) and the operation catalog (``IOperationCatalog``)
    are injected via the constructor (P3) — the planner no longer instantiates a
    provider itself, and the operation source is behind a port (P4).
    """

    def __init__(self, llm: ILLMService, catalog: IOperationCatalog) -> None:
        self._llm = llm
        self._catalog = catalog

    async def plan(self, user_prompt: str) -> ExecutionPlan:
        operations = self._catalog.list_operations()
        operations_prompt = build_operations_prompt(operations)
        system_prompt = build_plan_prompt(operations_prompt)

        response = await self._llm.get_chat_completion(system_prompt, user_prompt)
        data = json.loads(response)
        data["user_prompt"] = user_prompt
        return ExecutionPlan.from_planner_dict(data)

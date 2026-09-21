import importlib
import json
import pkgutil

from agent_sdk import OpenAIService
import app.layer1_domain.operations as operations
from app.layer2_application.features.execute_agent.operation_loader import build_operations_prompt
from app.layer2_application.features.execute_agent.prompt.transformation_planner_prompt import build_plan_prompt


def _load_operations():
    """Discover the domain operation definitions (Layer-1) available to the planner."""
    ops = []
    for _, module_name, _ in pkgutil.iter_modules(operations.__path__):
        module = importlib.import_module(f"{operations.__name__}.{module_name}")
        if hasattr(module, "OPERATION"):
            ops.append(module.OPERATION)
    return ops


class Planner:

    def __init__(self):
        self.llm = OpenAIService()

    async def plan(self, user_prompt: str, header: dict):
        operations_prompt = build_operations_prompt(_load_operations())
        system_prompt = build_plan_prompt(header, operations_prompt)
        response = await self.llm.get_chat_completion(system_prompt, user_prompt)
        return json.loads(response)

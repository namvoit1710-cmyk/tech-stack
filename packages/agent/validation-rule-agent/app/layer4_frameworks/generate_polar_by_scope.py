import json
import logging

from agent_sdk.layer4_frameworks.ai.openai_service import OpenAIService
from app.layer1_domain.entities.validation_rule_agent_data import ValidationRuleAgentData
from app.layer4_frameworks.generate_polar_column import generate_polar_column
from app.layer4_frameworks.generate_polar_row import generate_polar_row

logging.basicConfig(level=logging.INFO)


async def generate_polar_by_scope(
    long_input: str,
    llm: OpenAIService | None = None,
    header: dict | None = None,
) -> dict:
    if not long_input:
        return {"error": "Missing long_input"}

    llm = llm or OpenAIService()
    system_prompt = """
        You are a scope classifier for data transformation requests.

        Decide whether the user request should be handled by row transformations or column transformations.

        Return ONLY valid JSON in this exact format:
        {
            "scope": "row" | "column"
        }

        Choose "row" when the request is about:
        - inserting rows
        - deleting rows

        Choose "column" when the request is about:
        - updating cell values in an existing column
        - mapping or deriving columns
        - renaming columns
        - dropping columns
        - filtering data with column conditions

        If the request changes only some values inside a column, choose "column".
        If the request is ambiguous, choose the most likely scope based on the requested operation.

        Output ONLY JSON.
    """
    user_prompt = f"""
        User request:
        {long_input}
    """

    try:
        response = await llm.get_chat_completion(system_prompt, user_prompt)
        logging.info("[Polar_Scope] LLM response: %s", response)
        payload = json.loads(response)
        scope = str(payload.get("scope", "")).strip().lower()
    except Exception as exc:
        return {
            "error": "Failed to determine polar scope",
            "details": str(exc),
            "raw_response": response if "response" in locals() else None,
        }

    dispatchers = {
        "row": generate_polar_row,
        "column": generate_polar_column,
    }
    dispatcher = dispatchers.get(scope)
    if dispatcher is None:
        return ValidationRuleAgentData.invalid(
            message=f"Unable to determine transformation scope: {scope or 'unknown'}",
        ).to_dict()

    return await dispatcher(long_input, header=header)
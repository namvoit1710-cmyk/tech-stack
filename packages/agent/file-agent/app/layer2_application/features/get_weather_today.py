from agent_sdk import tool
from agent_sdk import OpenAIService
@tool("get_weather_today", internal=True)
async def get_weather_today(long_input: str = None) -> dict:
    """Get today's weather information.

    Args:
        long_input: The full user request text. Can be None.
    """
    llm = OpenAIService()
    if llm is None:
        raise ValueError("open_ai_service is required to get weather information.")

    system_prompt = (
        "You are a weather assistant. Respond with a JSON object containing today's weather information. "
        "Ensure the response is in JSON format."
    )

    try:
        response = await llm.get_chat_completion(system_prompt, "")
        return {
            "result": response,
        }
    except Exception as e:
        return {
            "status": "error",
            "reason": str(e),
        }
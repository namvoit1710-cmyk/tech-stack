from agent_sdk import tool


@tool(internal=True)
def minus(left: float, right: float) -> dict:
    """
    Subtract two numbers.

    Args:
        left: The first number.
        right: The second number.

    Returns:
        A dictionary containing the operation and result.
    """
    return {"operation": "minus", "left": left, "right": right, "result": left - right}

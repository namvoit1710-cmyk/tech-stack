from agent_sdk import tool


@tool(internal=True)
def plus(left: float, right: float) -> dict:
    """
    Add two numbers.

    Args:
        left: The first number.
        right: The second number.

    Returns:
        A dictionary containing the operation, operands, and result.
    """
    return {"operation": "plus", "left": left, "right": right, "result": left + right}

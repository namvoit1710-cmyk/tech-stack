from agent_sdk import tool


@tool
def echo_tool(input_text: str) -> str:
    """Echo the input back. Used to verify the local tool execution path."""
    return f"echo: {input_text}"

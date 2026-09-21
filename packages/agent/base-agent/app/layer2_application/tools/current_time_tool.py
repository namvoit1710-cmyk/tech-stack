from datetime import datetime

from app.layer2_application.interfaces.register_tool import tool


@tool
def get_current_time() -> str:
    """Return the current system time."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

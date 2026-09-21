from collections.abc import Callable
from typing import TypeVar

AppToolFunc = TypeVar("AppToolFunc", bound=Callable)

APP_TOOL_METADATA = "__is_app_tool__"


def tool(func: AppToolFunc) -> AppToolFunc:
    setattr(func, APP_TOOL_METADATA, True)
    return func
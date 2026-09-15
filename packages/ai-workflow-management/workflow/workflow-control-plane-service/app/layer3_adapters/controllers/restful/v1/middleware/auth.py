from fastapi import Request
from typing import Any


async def auth_middleware(request: Request, call_next: Any) -> Any:
    # Placeholder: pass-through authentication
    response = await call_next(request)
    return response

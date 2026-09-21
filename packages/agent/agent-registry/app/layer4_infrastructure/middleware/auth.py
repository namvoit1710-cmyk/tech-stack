# infrastructure/middlewares/auth_middleware.py

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request


class AuthMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):

        auth_header = request.headers.get("Authorization")

        if auth_header:
            request.state.jwt_token = auth_header.replace(
                "Bearer ",
                ""
            )
        else:
            request.state.jwt_token = None

        response = await call_next(request)

        return response
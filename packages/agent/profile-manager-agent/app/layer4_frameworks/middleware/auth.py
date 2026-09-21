from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
from app.layer2_application.interfaces.token_provider_port import TokenProviderPort


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token_provider: TokenProviderPort):
        super().__init__(app)
        self._token_provider = token_provider

    async def dispatch(self, request: Request, call_next):
        auth_header = request.headers.get("Authorization")
        if auth_header:
            request.state.jwt_token = auth_header.replace("Bearer ", "")
        else:
            request.state.jwt_token = await self._token_provider.get_token()

        return await call_next(request)
import httpx
from app.layer1_domain.entities.exceptions import UnauthorizedException
from app.layer4_frameworks.config.settings_adapter import settings
from app.layer2_application.interfaces.token_provider_port import TokenProviderPort


class OAuthTokenProvider(TokenProviderPort):
    async def get_token(self) -> str:
        if not settings.TOKEN_URL:
            raise UnauthorizedException("No token source configured.")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    settings.TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": settings.CLIENT_ID,
                        "client_secret": settings.CLIENT_SECRET,
                    },
                )
                resp.raise_for_status()
                return resp.json()["access_token"]
        except UnauthorizedException:
            raise
        except Exception:
            raise UnauthorizedException("Failed to obtain token from token_url.")

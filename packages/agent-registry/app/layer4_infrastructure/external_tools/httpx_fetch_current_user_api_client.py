"""HTTPX client for fetching workflow definitions from external Workflow Control Plane API."""
from urllib.parse import quote

import httpx

from app.layer1_domain.exceptions import InvalidDataException
from app.layer2_application.dtos.user_info_fetch_dto import UserInfoFetchDTO, UserGroupInfoFetchDTO, UserGroupDetailFetchDTO

from app.layer2_application.interfaces.fetch_current_user_info_api_client import IFetchCurrentUserInfoApiClient
from app.layer2_application.interfaces.fetch_current_user_info_setting_port import FetchCurrentUserInfoClientSettings
from app.layer3_presentation.schemas.user_schema import UserInfoFetchResponse, UserGroupsFetchResponse, UserGroupDetailFetchResponse

class HttpxFetchCurrentUserInfoApiClient(IFetchCurrentUserInfoApiClient):
    """HTTPX implementation of IFetchCurrentUserInfoApiClient.
    
    This client uses HTTPX to make asynchronous HTTP requests to the Profile Management Service.
    It translates the API responses into domain User Info entities.
    """

    def __init__(self, settings: FetchCurrentUserInfoClientSettings):
        """Initialize the client with the base URL of the external API."""
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.settings.fetch_timeout_seconds)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_current_user_info(self, token: str) -> UserInfoFetchDTO:
        """Fetch current user info from the external API using HTTPX."""
        try:
            url = self.settings.fetch_current_user_info_url
            headers = {"Authorization": f"Bearer {token}"}
            client = await self._get_client()
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            payload = response.json()
            valid_payload = UserInfoFetchResponse.model_validate(payload)  # Validate response structure

            return UserInfoFetchDTO.from_api_response(valid_payload.model_dump())
        except httpx.HTTPError as e:
            raise InvalidDataException(f"Failed to fetch current user info data from Profile Management Service: {e}")
        except Exception as e:
            raise InvalidDataException(f"An error occurred while fetching current user info data from Profile Management Service: {e}")
        
    async def fetch_user_by_external_id(self, token: str, external_id: str) -> UserInfoFetchDTO:
        """Fetch a specific user's info by external_id (PM get-user-by-id) using HTTPX."""
        try:
            if not self.settings.fetch_user_by_id_url:
                raise InvalidDataException("PM get-user-by-id URL (FETCH_USER_BY_ID_URL) is not configured")
            # external_id is a caller-supplied path param; percent-encode it before
            # interpolating into the outbound PM URL (no query/fragment/path injection).
            safe_external_id = quote(str(external_id), safe="")
            url = self.settings.fetch_user_by_id_url.format(external_id=safe_external_id)
            headers = {"Authorization": f"Bearer {token}"}
            client = await self._get_client()
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            payload = response.json()
            valid_payload = UserInfoFetchResponse.model_validate(payload)

            return UserInfoFetchDTO.from_api_response(valid_payload.model_dump())
        except httpx.HTTPError as e:
            raise InvalidDataException(f"Failed to fetch user '{external_id}' from Profile Management Service: {e}")
        except InvalidDataException:
            raise
        except Exception as e:
            raise InvalidDataException(f"An error occurred while fetching user '{external_id}' from Profile Management Service: {e}")

    async def fetch_current_user_groups(self, token: str) -> list[UserGroupInfoFetchDTO]:
        """Fetch current user's group memberships from the external API using HTTPX."""
        try:
            url = self.settings.fetch_current_user_groups_url
            headers = {"Authorization": f"Bearer {token}"}
            client = await self._get_client()
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            payload = response.json()
            valid_payload = UserGroupsFetchResponse.model_validate(payload)  # Validate response structure
            
            return [UserGroupInfoFetchDTO.from_api_response(item.model_dump()) for item in valid_payload.user_groups]
        except httpx.HTTPError as e:
            raise InvalidDataException(f"Failed to fetch current user groups from Profile Management Service: {e}")
        except Exception as e:
            raise InvalidDataException(f"An error occurred while fetching current user groups from Profile Management Service: {e}")
        
    async def fetch_current_user_group_detail(self, token: str, group_id: str) -> UserGroupDetailFetchDTO:
        """Fetch details of a specific user group from the external API using HTTPX."""
        try:
            url = self.settings.fetch_current_user_groups_details_url.format(group_id=group_id)
            headers = {"Authorization": f"Bearer {token}"}
            client = await self._get_client()
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            payload = response.json()
            valid_payload = UserGroupDetailFetchResponse.model_validate(payload)  # Validate response structure
            
            return UserGroupDetailFetchDTO.from_api_response(valid_payload.user_group.model_dump())
        except httpx.HTTPError as e:
            raise InvalidDataException(f"Failed to fetch user group detail from Profile Management Service: {e}")
        except Exception as e:
            raise InvalidDataException(f"An error occurred while fetching user group detail from Profile Management Service: {e}")
from typing import Protocol

from app.layer2_application.dtos.user_info_fetch_dto import UserGroupDetailFetchDTO, UserGroupInfoFetchDTO, UserInfoFetchDTO


class IFetchCurrentUserInfoApiClient(Protocol):
    """Interface for fetching current user info from an external API.
    
    This port defines the contract for interacting with the user info API.
    Infrastructure layer provides the implementation (e.g., using HTTP client).
    """
    async def fetch_current_user_info(self, token: str) -> UserInfoFetchDTO:
        """Fetch current user info from the external API.

        Args:
            token: JWT token of the user

        Returns:
            A user info DTO

        Raises:
            InvalidDataException: If the API response is invalid or if there's an error during fetching
        """
        ...

    async def fetch_user_by_external_id(self, token: str, external_id: str) -> UserInfoFetchDTO:
        """Fetch a specific user's info by external_id (PM SCIM get-user-by-id).

        Used by assign-role to mirror a target the caller is assigning. Called with the
        caller's own token (no borrowed credentials).

        Args:
            token: JWT token of the caller
            external_id: stable IAS/PM id of the target user

        Returns:
            A user info DTO

        Raises:
            InvalidDataException: If the API response is invalid or there's a fetch error
        """
        ...
        
    async def fetch_current_user_groups(self, token: str) -> list[UserGroupInfoFetchDTO]:
        """Fetch current user's group memberships from the external API.
        
        Args:
            token: JWT token of the user
        
        Returns:
            A list of user group info DTOs
            
        Raises:
            InvalidDataException: If the API response is invalid or if there's an error during fetching
        """
        ...
        
    async def fetch_current_user_group_detail(self, token: str, group_id: str) -> UserGroupDetailFetchDTO:
        """Fetch details of a specific user group from the external API.
        
        Args:
            token: JWT token of the user
            group_id: ID of the user group to fetch details for
        
        Returns:
            A user group detail DTO
            
        Raises:
            InvalidDataException: If the API response is invalid or if there's an error during fetching
        """
        ...
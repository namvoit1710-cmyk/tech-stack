"""User Info Fetch DTO."""
from dataclasses import Field, dataclass

@dataclass
class UserInfoFetchDTO:
    """Data transfer object for fetching current user info from external API."""
    first_name: str
    last_name: str
    email: str
    name: str
    display_name: str
    tenant_id: str
    
    @classmethod
    def from_api_response(cls, response: dict) -> "UserInfoFetchDTO":
        """Create DTO from API response data.
        
        Args:
            response: Dictionary with API response data
            
        Returns:
            UserInfoFetchDTO instance
        """
        return cls(
            first_name=response.get("first_name", ""),
            last_name=response.get("last_name", ""),
            name=response.get("name", ""),
            email=response.get("email", ""),
            display_name=response.get("display_name", ""),
            tenant_id=response.get("tenant_id", "")
        )

@dataclass
class UserGroupInfoFetchDTO:
    """Data transfer object for fetching user group info from external API."""
    group_id: str
    group_name: str
    
    @classmethod
    def from_api_response(cls, response: dict) -> "UserGroupInfoFetchDTO":
        """Create DTO from API response data.
        
        Args:
            response: Dictionary with API response data
            
        Returns:
            UserGroupInfoFetchDTO instance
        """
        return cls(
            group_id=response.get("group_id", ""),
            group_name=response.get("group_name", "")
        )
    
@dataclass
class UserGroupDetailFetchDTO:
    """Data transfer object for fetching user group detail info from external API."""
    group_id: str
    group_name: str
    roles: list[str]
    
    @classmethod
    def from_api_response(cls, response: dict) -> "UserGroupDetailFetchDTO":
        """Create DTO from API response data.
        
        Args:
            response: Dictionary with API response data
            
        Returns:
            UserGroupDetailFetchDTO instance
        """
        return cls(
            group_id=response.get("group_id", ""),
            group_name=response.get("group_name", ""),
            roles=response.get("roles", [])
        )
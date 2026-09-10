"""User schema definitions for API responses."""
from pydantic import BaseModel, Field, AliasChoices

class UserInfoFetchResponse(BaseModel):
    """Response schema for user info."""
    
    first_name: str = Field(validation_alias=AliasChoices("first_name", "firstName", "firstname"))
    last_name: str = Field(validation_alias=AliasChoices("last_name", "lastName", "lastname"))
    email: str = Field(validation_alias=AliasChoices("email", "Email"))
    name: str = Field(validation_alias=AliasChoices("name", "Name"))
    display_name: str = Field(validation_alias=AliasChoices("display_name", "displayName", "displayname"))
    tenant_id: str = Field(validation_alias=AliasChoices("tenant_id", "tenantId", "tenantID", "tenantid"))

class UserGroupEntry(BaseModel):
    """Schema for a single user group entry."""
    
    group_id: str = Field(validation_alias=AliasChoices("group_id", "id", "ID", "groupid", "groupId", "groupID"))
    group_name: str = Field(validation_alias=AliasChoices("group_name", "groupName", "display_name", "displayName", "displayname", "groupname"))

class UserGroupsFetchResponse(BaseModel):
    """Response schema for user groups."""
    
    user_groups: list[UserGroupEntry] = Field(validation_alias=AliasChoices("groups", "Groups", "user_groups", "userGroups"))
    
class UserGroupDetailEntry(BaseModel):
    """Schema for a single user group detail entry."""
    
    group_id: str = Field(validation_alias=AliasChoices("group_id", "id", "ID", "groupid", "groupId", "groupID"))
    group_name: str = Field(validation_alias=AliasChoices("group_name", "groupName", "display_name", "displayName", "displayname", "groupname"))
    roles: list[str] = Field(validation_alias=AliasChoices("roles", "Roles"))
    
class UserGroupDetailFetchResponse(BaseModel):
    """Response schema for detailed user group information."""
    
    user_group: UserGroupDetailEntry = Field(validation_alias=AliasChoices("group", "Group", "user_group", "userGroup"))
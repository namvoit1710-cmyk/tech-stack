"""User profile repository port (ADR-005)."""

from typing import Protocol, Optional

from app.layer1_domain.entities.permission.user_profile import UserProfile


class IUserProfileRepository(Protocol):
    def get(self, user_id: str, tenant_id: str) -> Optional[UserProfile]: ...
    def save(self, profile: UserProfile) -> None: ...
    def delete(self, user_id: str, tenant_id: str) -> None: ...

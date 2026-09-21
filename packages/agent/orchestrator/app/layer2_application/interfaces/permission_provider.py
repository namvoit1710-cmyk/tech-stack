from typing import Protocol


class IPermissionProvider(Protocol):
    def get_user_permissions(self, user_id: str) -> dict: ...

    def get_user_features(self, user_email: str, authorization_header: str = "") -> dict: ...

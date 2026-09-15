from __future__ import annotations

from enum import Enum


class AuthType(str, Enum):
    BEARER = "bearer"
    API_KEY_HEADER = "api_key_header"
    OAUTH_CC = "oauth_cc"
    NONE = "none"


# Required keys in the decrypted auth_config JSON, keyed by AuthType.
# Used by validation when a credential is set or rotated.
AUTH_CONFIG_REQUIRED_KEYS: dict[AuthType, frozenset[str]] = {
    AuthType.BEARER: frozenset({"token"}),
    AuthType.API_KEY_HEADER: frozenset({"header", "value"}),
    AuthType.OAUTH_CC: frozenset({"token_url", "client_id", "client_secret"}),
    AuthType.NONE: frozenset(),
}

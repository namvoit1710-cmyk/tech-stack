from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.layer1_domain.value_objects.auth_type import AuthType


@dataclass(frozen=True)
class AuthHeader:
    """A concrete HTTP auth header (name, value) the worker injects upstream."""

    name: str
    value: str


def materialize_auth_header(
    auth_type: AuthType, plaintext: dict[str, Any]
) -> AuthHeader | None:
    """Domain rule: turn a decrypted auth_config into the HTTP header to inject.

    Keeps "how a credential becomes a header" in the domain. The caller (resolve
    use case) supplies the already-decrypted plaintext; this never touches the
    cipher. Returns ``None`` for ``AuthType.NONE``.
    """
    if auth_type is AuthType.NONE:
        return None
    if auth_type is AuthType.BEARER:
        return AuthHeader("Authorization", f"Bearer {plaintext.get('token', '')}")
    if auth_type is AuthType.API_KEY_HEADER:
        return AuthHeader(str(plaintext.get("header", "")), str(plaintext.get("value", "")))
    if auth_type is AuthType.OAUTH_CC:
        # The client-credentials token exchange is not implemented yet.
        raise NotImplementedError("oauth_cc auth materialization is not implemented")
    raise NotImplementedError(f"unhandled auth_type: {auth_type}")

from __future__ import annotations

from typing import Any

from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.auth_type import (
    AUTH_CONFIG_REQUIRED_KEYS,
    AuthType,
)


def validate_auth_config(auth_type: AuthType, plaintext: dict[str, Any]) -> None:
    """Domain rule: a plaintext auth_config must carry the keys its AuthType needs.

    Raises ``ValidationError`` on a missing/empty required key. This keeps "what
    is a valid credential shape" in the domain; the cipher (how to encrypt) stays
    in infrastructure, and the domain never stores plaintext.
    """
    required = AUTH_CONFIG_REQUIRED_KEYS[auth_type]
    missing = [k for k in required if k not in plaintext or plaintext[k] == ""]
    if missing:
        raise ValidationError(
            f"auth_config missing required keys for {auth_type.value}: {missing}"
        )

"""JWT identity reader — extracts external_id from the bearer token (SA RBAC v1, Task 2).

Reads ONLY the user id claim. The signature is NOT verified here: authentication happens
at the gateway/approuter upstream, and this service only needs the caller's stable id to
key the user mirror. Implemented with a dependency-free base64url decode of the JWT
payload segment (PyJWT is not a dependency of this service).

As cheap defense-in-depth (the security model otherwise rests entirely on the gateway),
the reader ALSO honours the token's `exp` claim locally (with a small clock-skew leeway):
an expired token yields no id → the caller returns 401 rather than resolving an expired
token to a full principal. Signature verification remains delegated upstream.

Claim precedence follows the sibling orchestrator service: `user_uuid` first, then
`sub` as a fallback. A non-string id claim (e.g. a numeric `sub` emitted by some IdPs)
is treated as absent → 401; the Registry mirror keys on string ids only.
"""

import base64
import binascii
import json
import time

from app.layer2_application.interfaces.identity_token_reader_port import IIdentityTokenReader
from app.layer4_infrastructure.logger.app_logger import get_logger

logger = get_logger(__name__)

# Claim names carrying the stable user id, in precedence order.
_ID_CLAIMS = ("user_uuid", "sub")

# Clock-skew tolerance for the local exp check (seconds). Generous because the gateway
# is the authoritative validator; this is only a secondary backstop.
_EXP_LEEWAY_SECONDS = 60


class JwtIdentityTokenReader(IIdentityTokenReader):
    """Extracts the caller's external_id from an (already-authenticated) JWT."""

    def extract_user_uuid(self, token: str) -> str | None:
        payload = self._decode_payload(token)
        if payload is None:
            return None
        if self._is_expired(payload):
            return None
        for claim in _ID_CLAIMS:
            value = payload.get(claim)
            if isinstance(value, str) and value.strip():
                return value
        return None

    @staticmethod
    def _decode_payload(token: str) -> dict | None:
        """Decode the JWT payload segment without verifying the signature.

        Returns None (never raises) for anything that is not a well-formed JWT with a
        JSON object payload.
        """
        if not token or not isinstance(token, str):
            return None
        parts = token.split(".")
        if len(parts) != 3:
            return None
        segment = parts[1]
        # base64url requires padding to a multiple of 4.
        padding = "=" * (-len(segment) % 4)
        try:
            decoded = base64.urlsafe_b64decode(segment + padding)
            payload = json.loads(decoded)
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    @staticmethod
    def _is_expired(payload: dict) -> bool:
        """Local exp backstop. Absent/unparseable exp → not expired (gateway is authoritative)."""
        exp = payload.get("exp")
        if exp is None:
            return False
        try:
            return time.time() > float(exp) + _EXP_LEEWAY_SECONDS
        except (TypeError, ValueError):
            return False

"""Identity-token reader port (SA RBAC v1, Task 2).

Extracts ONLY the stable user id (external_id) from the request JWT. Deliberately
narrow: the Registry reads no other claim from the token (email/name/tenant come from
Profile Management). This isolates id-extraction so that when the gateway later sends
the user id directly (instead of a JWT), only the implementation swaps (D11).
"""

from typing import Protocol


class IIdentityTokenReader(Protocol):
    """Reads the caller's external_id from a bearer token."""

    def extract_user_uuid(self, token: str) -> str | None:
        """Return the caller's stable external id, or None if the token has none.

        Must not raise on a malformed token — returns None so the caller can decide
        (401). Does not verify the signature (the gateway authenticates upstream).
        """
        ...

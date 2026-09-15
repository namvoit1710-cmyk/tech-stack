from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EncryptedCredential:
    """Ciphertext + the KEK version that produced it.

    `ciphertext_b64` is the AES-GCM output (nonce || ciphertext || tag), base64-encoded.
    `key_version` selects which `GATEWAY_REGISTRY_KEK_V<n>` env var decrypts it.
    """
    ciphertext_b64: str
    key_version: int

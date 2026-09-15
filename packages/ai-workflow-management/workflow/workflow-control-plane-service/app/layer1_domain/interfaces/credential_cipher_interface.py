from __future__ import annotations

from typing import Any, Protocol

from app.layer1_domain.value_objects.encrypted_credential import EncryptedCredential


class DecryptionFailed(Exception):
    """Raised when ciphertext cannot be decrypted (wrong key, corruption, tampering)."""


class ICredentialCipher(Protocol):
    def encrypt(self, plaintext: dict[str, Any]) -> EncryptedCredential: ...
    def decrypt(self, encrypted: EncryptedCredential) -> dict[str, Any]: ...
    @property
    def current_key_version(self) -> int: ...

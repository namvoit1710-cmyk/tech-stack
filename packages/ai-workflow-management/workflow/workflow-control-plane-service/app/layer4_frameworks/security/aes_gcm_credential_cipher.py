"""AES-256-GCM impl of ICredentialCipher.

KEK material is loaded from env. Each version `n` lives in `GATEWAY_REGISTRY_KEK_V<n>`
(base64-encoded 32 bytes). The cipher writes new ciphertexts with `current_key_version`
and decrypts existing ciphertexts via the key version stamped on the row, so rotation
is online: bump CURRENT to v+1, leave v's env var in place, and re-encrypt-on-read
will eventually promote old rows.

The format inside `EncryptedCredential.ciphertext_b64` is base64(nonce || ciphertext+tag),
where the nonce is 12 random bytes per encryption.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.layer1_domain.interfaces.credential_cipher_interface import (
    DecryptionFailed,
    ICredentialCipher,
)
from app.layer1_domain.value_objects.encrypted_credential import EncryptedCredential


_CURRENT_VERSION_ENV = "GATEWAY_REGISTRY_KEK_CURRENT_VERSION"
_KEY_ENV_PREFIX = "GATEWAY_REGISTRY_KEK_V"
_NONCE_BYTES = 12
_KEY_BYTES = 32


class AesGcmCredentialCipher(ICredentialCipher):
    def __init__(
        self,
        *,
        current_version: int | None = None,
        keys: dict[int, bytes] | None = None,
    ) -> None:
        """Direct construction is for tests; production callers use `from_env`."""
        if keys is None or not keys:
            raise ValueError("at least one KEK version must be provided")
        if current_version is None:
            current_version = max(keys)
        if current_version not in keys:
            raise ValueError(
                f"current_version {current_version} has no key material in keys map"
            )
        for v, k in keys.items():
            if len(k) != _KEY_BYTES:
                raise ValueError(
                    f"KEK v{v} must be {_KEY_BYTES} bytes, got {len(k)}"
                )
        self._keys = dict(keys)
        self._current_version = current_version

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "AesGcmCredentialCipher":
        env = environ if environ is not None else os.environ
        keys: dict[int, bytes] = {}
        for k, v in env.items():
            if not k.startswith(_KEY_ENV_PREFIX):
                continue
            suffix = k[len(_KEY_ENV_PREFIX):]
            if not suffix.isdigit():
                continue
            try:
                raw = base64.b64decode(v)
            except Exception as exc:
                raise ValueError(f"env {k} is not valid base64: {exc}") from exc
            keys[int(suffix)] = raw
        if not keys:
            raise ValueError(
                f"no KEK versions found in env (expected {_KEY_ENV_PREFIX}<n> entries)"
            )
        current_raw = env.get(_CURRENT_VERSION_ENV)
        current_version = int(current_raw) if current_raw else max(keys)
        return cls(current_version=current_version, keys=keys)

    @property
    def current_key_version(self) -> int:
        return self._current_version

    def encrypt(self, plaintext: dict[str, Any]) -> EncryptedCredential:
        key = self._keys[self._current_version]
        nonce = secrets.token_bytes(_NONCE_BYTES)
        plaintext_bytes = json.dumps(plaintext, separators=(",", ":")).encode("utf-8")
        ct = AESGCM(key).encrypt(nonce, plaintext_bytes, associated_data=None)
        blob = base64.b64encode(nonce + ct).decode("ascii")
        return EncryptedCredential(
            ciphertext_b64=blob,
            key_version=self._current_version,
        )

    def decrypt(self, encrypted: EncryptedCredential) -> dict[str, Any]:
        key = self._keys.get(encrypted.key_version)
        if key is None:
            raise DecryptionFailed(
                f"no key material loaded for version {encrypted.key_version}"
            )
        try:
            raw = base64.b64decode(encrypted.ciphertext_b64)
        except Exception as exc:
            raise DecryptionFailed(f"ciphertext is not valid base64: {exc}") from exc
        if len(raw) < _NONCE_BYTES + 16:
            raise DecryptionFailed("ciphertext shorter than nonce + tag")
        nonce, ct = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        try:
            plaintext_bytes = AESGCM(key).decrypt(nonce, ct, associated_data=None)
        except InvalidTag as exc:
            raise DecryptionFailed("authentication tag mismatch") from exc
        try:
            return json.loads(plaintext_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DecryptionFailed(f"plaintext is not valid JSON: {exc}") from exc

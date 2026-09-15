"""Unit tests for AesGcmCredentialCipher."""
from __future__ import annotations

import base64

import pytest

from app.layer1_domain.interfaces.credential_cipher_interface import DecryptionFailed
from app.layer1_domain.value_objects.encrypted_credential import EncryptedCredential
from app.layer4_frameworks.security.aes_gcm_credential_cipher import (
    AesGcmCredentialCipher,
)


def _k(seed: int) -> bytes:
    return bytes([seed % 256] * 32)


@pytest.mark.unit
def test_round_trip_returns_original_plaintext() -> None:
    cipher = AesGcmCredentialCipher(current_version=1, keys={1: _k(1)})
    plaintext = {"token": "abc.def.ghi", "scope": ["a", "b"]}

    encrypted = cipher.encrypt(plaintext)
    decrypted = cipher.decrypt(encrypted)

    assert decrypted == plaintext
    assert encrypted.key_version == 1


@pytest.mark.unit
def test_encrypt_uses_fresh_nonce_each_call() -> None:
    cipher = AesGcmCredentialCipher(current_version=1, keys={1: _k(1)})
    a = cipher.encrypt({"token": "x"})
    b = cipher.encrypt({"token": "x"})
    assert a.ciphertext_b64 != b.ciphertext_b64


@pytest.mark.unit
def test_decrypt_with_wrong_key_version_fails() -> None:
    enc_cipher = AesGcmCredentialCipher(current_version=1, keys={1: _k(1)})
    encrypted = enc_cipher.encrypt({"token": "x"})

    dec_cipher = AesGcmCredentialCipher(current_version=2, keys={2: _k(2)})
    with pytest.raises(DecryptionFailed):
        dec_cipher.decrypt(encrypted)


@pytest.mark.unit
def test_decrypt_with_tampered_ciphertext_fails() -> None:
    cipher = AesGcmCredentialCipher(current_version=1, keys={1: _k(1)})
    encrypted = cipher.encrypt({"token": "x"})

    raw = base64.b64decode(encrypted.ciphertext_b64)
    tampered = raw[:-1] + bytes([raw[-1] ^ 0x01])
    bad = EncryptedCredential(
        ciphertext_b64=base64.b64encode(tampered).decode("ascii"),
        key_version=1,
    )
    with pytest.raises(DecryptionFailed):
        cipher.decrypt(bad)


@pytest.mark.unit
def test_multi_version_can_decrypt_older_rows() -> None:
    """Rotation path: old rows decrypt via their stamped version; new rows use current."""
    cipher = AesGcmCredentialCipher(current_version=2, keys={1: _k(1), 2: _k(2)})

    old_cipher = AesGcmCredentialCipher(current_version=1, keys={1: _k(1)})
    old_encrypted = old_cipher.encrypt({"token": "old"})

    assert cipher.decrypt(old_encrypted) == {"token": "old"}

    new_encrypted = cipher.encrypt({"token": "new"})
    assert new_encrypted.key_version == 2
    assert cipher.decrypt(new_encrypted) == {"token": "new"}


@pytest.mark.unit
def test_from_env_loads_keys_and_picks_current_version() -> None:
    env = {
        "GATEWAY_REGISTRY_KEK_V1": base64.b64encode(_k(1)).decode("ascii"),
        "GATEWAY_REGISTRY_KEK_V2": base64.b64encode(_k(2)).decode("ascii"),
        "GATEWAY_REGISTRY_KEK_CURRENT_VERSION": "2",
    }
    cipher = AesGcmCredentialCipher.from_env(env)
    assert cipher.current_key_version == 2

    encrypted = cipher.encrypt({"token": "x"})
    assert encrypted.key_version == 2
    assert cipher.decrypt(encrypted) == {"token": "x"}


@pytest.mark.unit
def test_from_env_without_explicit_current_picks_max() -> None:
    env = {
        "GATEWAY_REGISTRY_KEK_V1": base64.b64encode(_k(1)).decode("ascii"),
        "GATEWAY_REGISTRY_KEK_V3": base64.b64encode(_k(3)).decode("ascii"),
    }
    cipher = AesGcmCredentialCipher.from_env(env)
    assert cipher.current_key_version == 3


@pytest.mark.unit
def test_from_env_raises_when_no_keys_present() -> None:
    with pytest.raises(ValueError, match="no KEK versions"):
        AesGcmCredentialCipher.from_env({})


@pytest.mark.unit
def test_wrong_size_key_rejected() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        AesGcmCredentialCipher(current_version=1, keys={1: b"\x00" * 16})

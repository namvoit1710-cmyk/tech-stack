"""Bootstrap wiring for the vault credential client — the activation flag.

CREDENTIALS_VAULT_URL flips the StartRun producer on; unset = dormant (None).
"""

from types import SimpleNamespace

from bootstrap import _build_vault_credential_client


def test_dormant_without_url():
    assert _build_vault_credential_client(SimpleNamespace()) is None
    assert _build_vault_credential_client(SimpleNamespace(CREDENTIALS_VAULT_URL="")) is None
    assert _build_vault_credential_client(SimpleNamespace(CREDENTIALS_VAULT_URL="   ")) is None


def test_enabled_with_url():
    client = _build_vault_credential_client(
        SimpleNamespace(CREDENTIALS_VAULT_URL="http://vault.test", CREDENTIALS_VAULT_SERVICE_TOKEN="tok")
    )
    assert client is not None
    assert client._base_url == "http://vault.test"
    assert client._service_token == "tok"


def test_enabled_without_token():
    client = _build_vault_credential_client(
        SimpleNamespace(CREDENTIALS_VAULT_URL="http://vault.test")
    )
    assert client is not None
    assert client._service_token is None

"""The design-time invoke path must hand workers a CANONICAL credential shape.

The FE nests the picked credential under ``params.args`` — autoform's
``buildPostBody`` does ``params.args = {...depParams}`` — but every worker reads
it with ``resolve_effective_auth``, which looks for a TOP-LEVEL
``credential_ref`` and otherwise falls back to the literal ``"default"``. Since
``__credentials`` is keyed by the real ref, that fallback finds nothing and every
credential-bearing design-time function (jira pickers, the Teams webhook test)
fails closed with "no credential was found".

Mirroring the ref up here fixes it once for all workers, at the layer that
already had to locate the ref in order to resolve it.
"""

from types import SimpleNamespace

import pytest

from app.layer3_adapters.controllers.restful.v1.worker_function_controller import (
    _resolve_credentials_into_params,
)

_ENTRY = {"type": "webhook_url", "config": {"url": "https://acme/hook"}}


class _Vault:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def resolve_by_id(self, tenant_id, credential_id, user_id=None):
        self.calls.append((tenant_id, credential_id, user_id))
        return dict(_ENTRY)


def _request(vault) -> SimpleNamespace:
    container = {"_vault_credential_client": vault}
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(container=container)))


@pytest.mark.asyncio
async def test_nested_args_ref_is_mirrored_to_top_level():
    vault = _Vault()
    params = {"function_id": "test_webhook", "args": {"credential_ref": "cred_1"}}

    out = await _resolve_credentials_into_params(_request(vault), params, "t1")

    # Both halves of what resolve_effective_auth reads must be flat and agree.
    assert out["credential_ref"] == "cred_1"
    assert out["__credentials"]["cred_1"] == _ENTRY
    # The nested original is left alone — dependency args still reach the handler.
    assert out["args"] == {"credential_ref": "cred_1"}
    assert vault.calls == [("t1", "cred_1", None)]


@pytest.mark.asyncio
async def test_flat_ref_still_resolves_and_is_not_clobbered():
    vault = _Vault()
    params = {"credential_ref": "cred_flat"}

    out = await _resolve_credentials_into_params(_request(vault), params, "t1")

    assert out["credential_ref"] == "cred_flat"
    assert out["__credentials"]["cred_flat"] == _ENTRY


@pytest.mark.asyncio
async def test_no_ref_is_passed_through_untouched():
    vault = _Vault()
    params = {"args": {"query": "abc"}}

    out = await _resolve_credentials_into_params(_request(vault), params, "t1")

    assert out == params
    assert "credential_ref" not in out
    assert "__credentials" not in out
    assert vault.calls == []  # no credential dependency -> no vault round-trip

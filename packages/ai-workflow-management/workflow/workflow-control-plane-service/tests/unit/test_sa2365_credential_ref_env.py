"""SA-2365 — a node's credential_ref may name its credential via {{$env.KEY}}.

The environment holds a credential-type variable (its value is a non-secret
credential REFERENCE id). A node sets ``credential_ref: {{$env.API_TOKEN}}``;
at dispatch the provider resolves that to the id from the run's $env source and
asks the vault for it. Switching environment switches the credential — no node
edit — and the secret still only ever lives at the vault seam.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.layer2_application.credentials.run_credential_provider import (
    RunCredentialProvider,
    _resolve_env_ref,
)


def _run(env=None):
    return SimpleNamespace(
        id="r1", root_run_id="r1", tenant_id="system",
        metadata={"user_id": "system"},
        context=SimpleNamespace(variables={"env": env} if env is not None else {}),
    )


def _node(credential_ref):
    return SimpleNamespace(
        id="n1", config={"credential_ref": credential_ref},
        parameters={}, input_binding={},
    )


class _FakeVault:
    def __init__(self):
        self.asked = []

    async def resolve_by_id(self, tenant_id, credential_id, user_id=None):
        self.asked.append(credential_id)
        return {"type": "bearer", "config": {}, "expires_at": None}


# --- the pure resolver -------------------------------------------------------

def test_resolve_env_ref_maps_expression_to_id():
    run = _run({"API_TOKEN": "cred-xyz"})
    assert _resolve_env_ref("{{$env.API_TOKEN}}", run) == "cred-xyz"


def test_resolve_env_ref_passes_literal_id_through():
    run = _run({"API_TOKEN": "cred-xyz"})
    assert _resolve_env_ref("cred-direct", run) == "cred-direct"


def test_resolve_env_ref_leaves_unresolvable_expression():
    run = _run({})  # no such key
    assert _resolve_env_ref("{{$env.MISSING}}", run) == "{{$env.MISSING}}"


# --- for_node end to end (fake vault) ---------------------------------------

@pytest.mark.asyncio
async def test_for_node_resolves_env_ref_then_asks_vault_for_the_id():
    vault = _FakeVault()
    provider = RunCredentialProvider(vault_client=vault)
    result = await provider.for_node(_node("{{$env.API_TOKEN}}"), _run({"API_TOKEN": "cred-xyz"}))
    assert vault.asked == ["cred-xyz"]                 # resolved id, not the expression
    assert result == {"cred-xyz": {"type": "bearer", "config": {}}}


@pytest.mark.asyncio
async def test_for_node_literal_ref_unchanged():
    vault = _FakeVault()
    provider = RunCredentialProvider(vault_client=vault)
    await provider.for_node(_node("cred-direct"), _run({"API_TOKEN": "cred-xyz"}))
    assert vault.asked == ["cred-direct"]

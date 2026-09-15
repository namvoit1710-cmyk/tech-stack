"""Phase 0 for retiring the pass-through credential — spec §13.6.

`DROP CONTEXT` is blocked by one key: `__credentials`. Post-SA-2287/B3 it holds
only what the caller of `POST /runs` supplied, and the one live supplier is
`executor-runtime-service`, which forwards a bearer token as the reserved
``"default"`` entry.

Removing that is safe **only** if nothing is borrowing it. One shape would break
silently:

* a node that declares a real ``auth_type`` and finds no credential fails
  **closed and loudly** — ``worker_sdk.resolve_effective_auth`` raises
  *"authentication is required but no credential was found"*;
* a node with ``auth_type`` ``none``/``""`` that is quietly borrowing the
  ``"default"`` entry would simply start going out **unauthenticated**. That is
  the bug ``resolve_effective_auth``'s own docstring was written about
  (``auth_type="none"`` is called out there as "a common FE default").

Static analysis cannot separate them: **142 of 3,344 workflow definitions carry
``bearer``/``Authorization`` with no ``credential_ref``**, and whether they land
on the fallback depends on runtime shape. The run data that would have answered
it was destroyed by the step-5a purge.

So this probe logs exactly that shape and nothing else. Silence over real
traffic is the precondition for phase 1.
"""
from __future__ import annotations

import pytest

from app.layer2_application.credentials.run_credential_provider import (
    DEFAULT_CREDENTIAL_REF,
    RunCredentialProvider,
)

PROBE = "PASSTHROUGH_CREDENTIAL_BORROWED"


class _Ctx:
    def __init__(self, variables): self.variables = variables


class _Run:
    def __init__(self, creds=None, run_id="run-1"):
        self.id = run_id
        self.context = _Ctx({"__credentials": creds} if creds is not None else {})


class _Node:
    def __init__(self, ref=None, node_id="n1"):
        self.id = node_id
        self.config = {"credential_ref": ref} if ref else {}


def _provider() -> RunCredentialProvider:
    # No vault, no cipher: overrides are read as plaintext, which is the shape
    # RunCredentialSeeder.seal writes when no cipher is configured.
    return RunCredentialProvider()


DEFAULT_ENTRY = {DEFAULT_CREDENTIAL_REF: {"type": "bearer", "config": {"token": "t"}}}


class TestItFiresOnExactlyTheDangerousShape:
    @pytest.mark.asyncio
    async def test_no_ref_plus_a_default_entry_logs(self, caplog):
        with caplog.at_level("WARNING"):
            await _provider().for_node(_Node(ref=None), _Run(DEFAULT_ENTRY))
        assert any(PROBE in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_the_line_names_the_run_and_node(self, caplog):
        """A probe that says 'somewhere, sometime' cannot be acted on — phase 1
        needs to know WHICH workflows to give a credential_ref."""
        with caplog.at_level("WARNING"):
            await _provider().for_node(
                _Node(ref=None, node_id="http-call"), _Run(DEFAULT_ENTRY, "run-42"))
        line = next(r.getMessage() for r in caplog.records if PROBE in r.message)
        assert "run-42" in line and "http-call" in line

    @pytest.mark.asyncio
    async def test_the_credential_is_still_handed_over(self, caplog):
        """Phase 0 observes; it must not change behaviour. Anything relying on
        the pass-through keeps working until phase 1 removes it deliberately."""
        got = await _provider().for_node(_Node(ref=None), _Run(DEFAULT_ENTRY))
        assert got == DEFAULT_ENTRY


class TestItStaysQuietOtherwise:
    @pytest.mark.asyncio
    async def test_a_node_with_its_own_ref_does_not_log(self, caplog):
        """It names what it wants — removing the pass-through cannot affect it."""
        with caplog.at_level("WARNING"):
            await _provider().for_node(_Node(ref="jira"), _Run(DEFAULT_ENTRY))
        assert not any(PROBE in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_no_default_entry_does_not_log(self, caplog):
        """The overwhelmingly common case: the agent passed no token, so
        ``seal()`` no-opped and the key is absent entirely."""
        with caplog.at_level("WARNING"):
            await _provider().for_node(_Node(ref=None), _Run(None))
        assert not any(PROBE in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_overrides_without_a_default_key_do_not_log(self, caplog):
        """A caller may supply a NAMED credential inline. That is not the
        fallback and is not what phase 1 removes."""
        with caplog.at_level("WARNING"):
            await _provider().for_node(
                _Node(ref=None), _Run({"jira": {"type": "bearer", "config": {}}}))
        assert not any(PROBE in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_a_run_with_no_context_does_not_explode(self, caplog):
        class Bare:
            id = "run-1"
        with caplog.at_level("WARNING"):
            assert await _provider().for_node(_Node(ref=None), Bare()) == {}


class TestTheConstantMatchesTheWorkerContract:
    def test_it_is_the_string_worker_sdk_falls_back_to(self):
        """``borrow_credential`` reads
        ``inputs["__credentials"][inputs.get("credential_ref") or "default"]``.
        Drift here and the probe watches a key nothing uses."""
        assert DEFAULT_CREDENTIAL_REF == "default"

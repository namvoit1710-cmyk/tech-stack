"""T4 — the shared worker credential coalesce (Q1 lift)."""

import pytest

from worker_sdk.layer2_application.services.effective_auth import (
    borrow_credential,
    resolve_effective_auth,
)


def _inputs_with_cred(ref="c1", entry=None):
    return {
        "__credentials": {ref: entry or {"type": "api_key",
                                         "config": {"header": "X-API-Key", "value": "k"}}},
        "credential_ref": ref,
    }


def test_none_auth_returns_none():
    assert resolve_effective_auth({}, auth_type="none") is None
    assert resolve_effective_auth({}, auth_type="") is None


def test_borrows_from_credentials_map():
    out = resolve_effective_auth(_inputs_with_cred(), auth_type="api_key")
    assert out["type"] == "api_key"
    assert out["config"]["value"] == "k"


def test_borrowed_credential_wins_over_none_auth_type():
    # Regression (401 bug): a selected credential must NOT be discarded when auth_type
    # says "none" (the FE default). The credential is the source of truth for auth; it
    # wins over auth_type. Previously this returned None → request sent unauthenticated
    # → 401. This is the invariant that keeps every worker from repeating the bug.
    out = resolve_effective_auth(_inputs_with_cred(), auth_type="none")
    assert out is not None, "credential must win over auth_type='none'"
    assert out["type"] == "api_key"
    assert out["config"]["value"] == "k"

    # Also wins over an empty auth_type and when no auth_type is passed at all.
    assert resolve_effective_auth(_inputs_with_cred(), auth_type="") is not None
    assert resolve_effective_auth(_inputs_with_cred()) is not None


def test_borrow_defaults_ref_to_default():
    inputs = {"__credentials": {"default": {"type": "bearer", "config": {"token": "t"}}}}
    assert borrow_credential(inputs)["config"]["token"] == "t"


def test_fail_closed_when_auth_required_and_nothing_found():
    with pytest.raises(ValueError):
        resolve_effective_auth({}, auth_type="bearer")
    with pytest.raises(ValueError):
        resolve_effective_auth({}, require=True)


def test_optional_returns_none_when_nothing_found():
    # No auth_type, no require, no borrowed credential: nothing to apply.
    assert resolve_effective_auth({}) is None

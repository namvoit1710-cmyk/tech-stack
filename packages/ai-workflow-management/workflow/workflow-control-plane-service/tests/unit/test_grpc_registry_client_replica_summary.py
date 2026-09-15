"""SA-2056 (R9) review fix — Finding 3 (Important), 2026-07-22 operator ruling.

``GrpcWorkerRegistryClient.get_replica_summary()`` used to return ``None``
unconditionally, documented as "unsupported by design" on the gRPC transport.
But every other method on this class (``list_workers``, ``get_worker``,
``get_worker_schema``, ...) already falls back to ``self._http_fallback`` when
gRPC can't answer, and that fallback is populated from ``WORKER_EXECUTION_URL``
under the normal ``TRANSPORT_MODE=grpc`` config (see the gRPC branch of
``create_messaging()`` in ``messaging/__init__.py``). Returning ``None``
unconditionally therefore made
R9's differ permanently dead on any gRPC-transport CP with the feed enabled,
even though ``HttpWorkerRegistryClient.get_replica_summary()`` would have
answered correctly.

Operator ruling: delegate to ``self._http_fallback`` in the same shape the
sibling methods use. The old "unsupported / warn-once" framing and its
``_replica_summary_warned`` latch go away — the feature is no longer
unsupported. If there is genuinely no fallback configured, ``None`` ("cannot
determine") is still correct, but it must not be silent about why.

Environment note: importing ``grpc_worker_registry_client`` requires
``workflow_proto.v1.worker_registry_pb2`` (generated protobuf stubs), which
live one level up from this service and are not on ``sys.path`` when pytest
runs from this service's root — hence ``pytest.importorskip`` below, same as
every other gRPC test file in this suite. Do NOT replace this with a
``sys.path`` hack: once ``workflow_proto`` is imported, Python caches it in
``sys.modules`` for the rest of the pytest process, so a forced import here
would silently un-skip every other ``importorskip``-gated gRPC test file too
(this bit a previous fix-pass on this exact file — see git history / R9
re-review notes). This module could not be executed in this environment for
that reason; see the fix report for what these assertions would show against
a real run.
"""
import logging

import pytest

pytest.importorskip("grpc", reason="grpcio not installed")
pytest.importorskip(
    "workflow_proto.v1.worker_registry_pb2", reason="workflow_proto not installed"
)

from app.layer4_frameworks.clients.grpc_worker_registry_client import (
    GrpcWorkerRegistryClient,
)


def _client(http_fallback_url=None) -> GrpcWorkerRegistryClient:
    # __init__ only sets plain attributes (the gRPC channel is opened
    # lazily inside _stub(), which get_replica_summary() never calls) so a
    # real construction is enough -- no channel, no network, no server.
    return GrpcWorkerRegistryClient("localhost:50052", http_fallback_url=http_fallback_url)


def test_get_replica_summary_delegates_to_http_fallback_when_configured(monkeypatch):
    """With a fallback configured (the normal TRANSPORT_MODE=grpc config, per
    messaging/__init__.py wiring WORKER_EXECUTION_URL through), the gRPC
    client must answer via HttpWorkerRegistryClient.get_replica_summary(),
    not go straight to None."""
    client = _client(http_fallback_url="http://executor:8004")

    sentinel_rows = [
        {"worker_type": "jira_search", "delivery_mode": "push",
         "replicas_total": 3, "replicas_healthy": 3, "oldest_heartbeat_age_seconds": 4.0},
    ]
    monkeypatch.setattr(client._http_fallback, "get_replica_summary", lambda: sentinel_rows)

    assert client.get_replica_summary() is sentinel_rows


def test_get_replica_summary_passes_through_none_from_fallback(monkeypatch):
    """Fallback unreachable -> None ("cannot determine") must pass straight
    through, not collapse into []."""
    client = _client(http_fallback_url="http://executor:8004")
    monkeypatch.setattr(client._http_fallback, "get_replica_summary", lambda: None)

    assert client.get_replica_summary() is None


def test_get_replica_summary_passes_through_genuine_empty_from_fallback(monkeypatch):
    """Fallback's genuine empty 200 ([]) must pass straight through, not
    collapse into None -- R9's differ needs [] to tombstone every type."""
    client = _client(http_fallback_url="http://executor:8004")
    monkeypatch.setattr(client._http_fallback, "get_replica_summary", lambda: [])

    result = client.get_replica_summary()
    assert result == [] and result is not None


def test_get_replica_summary_returns_none_and_warns_when_no_fallback_configured(caplog):
    """No http_fallback_url at all (misconfiguration, or a deployment that
    genuinely never set WORKER_EXECUTION_URL) -- None is still the correct
    "cannot determine" signal, but it must not be silent about why. No latch
    here: the old warn-once framing was for the now-removed "unsupported by
    design" state, not for this misconfiguration case."""
    client = _client(http_fallback_url=None)
    assert client._http_fallback is None

    with caplog.at_level(logging.WARNING):
        result = client.get_replica_summary()

    assert result is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "fallback" in warnings[0].message.lower()


def test_get_replica_summary_warns_every_call_when_no_fallback_configured(caplog):
    """Unlike the old latch (once per instance, ever), the no-fallback warning
    is not throttled -- it matches every other method on this class, none of
    which suppress repeat logging. Two calls -> two warnings."""
    client = _client(http_fallback_url=None)

    with caplog.at_level(logging.WARNING):
        client.get_replica_summary()
        client.get_replica_summary()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2


def test_no_replica_summary_warned_latch_attribute_remains():
    """The old `_replica_summary_warned` latch was specific to the
    "unsupported by design" framing this fix removes -- it must not linger
    as dead state on the instance."""
    client = _client()
    assert not hasattr(client, "_replica_summary_warned")

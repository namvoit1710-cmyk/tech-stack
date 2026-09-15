"""SA-1468 — explicit namespace requirement + thread-safe self-heal adoption.

Covers the three guarantees the story adds:

1. ``require_namespace`` turns an unset/empty namespace into a boot-time
   ``EventMeshConfigError`` — and the broker factory re-raises it instead of
   silently falling back to the mock broker.
2. Namespace self-heal is config-gated: with ``allow_namespace_self_heal``
   off, a 403 never mutates the shared ``EventMeshNames`` state.
3. Adoption is lock-guarded and double-checked: N simultaneous 403s adopt
   exactly once, with no torn value — and the "loser" callers still retry
   with the concurrently-adopted, correctly-qualified name.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.layer4_frameworks import _create_message_broker_client
from app.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)
from app.layer4_frameworks.messaging.event_mesh_config import (
    EventMeshConfig,
    EventMeshConfigError,
)
from app.layer4_frameworks.messaging.event_mesh_names import EventMeshNames


_NS_403_BODY = (
    "The queue=sub-orchestration-actions is not in the "
    "namespace=simplemdg/event/object-9 of the service instance "
    "id=89bf91ff-e2b0-40ef-ae98-3d03efea2210"
)
_ADOPTED = "simplemdg/event/object-9"


def _config_kwargs(**overrides) -> dict:
    defaults = dict(
        token_url="https://auth.example.com/oauth/token",
        messaging_url="https://messaging.example.com",
        management_url="https://management.example.com",
        client_id="test-id",
        client_secret="test-secret",
    )
    defaults.update(overrides)
    return defaults


class TestRequireNamespace:
    def test_empty_namespace_raises_when_required(self):
        with pytest.raises(EventMeshConfigError, match="EVENT_MESH_NAMESPACE is required"):
            EventMeshConfig(**_config_kwargs(namespace="", require_namespace=True))

    def test_whitespace_namespace_raises_when_required(self):
        with pytest.raises(EventMeshConfigError):
            EventMeshConfig(**_config_kwargs(namespace="   ", require_namespace=True))

    def test_set_namespace_passes_when_required(self):
        cfg = EventMeshConfig(
            **_config_kwargs(namespace="simplemdg/event/core-2", require_namespace=True)
        )
        assert cfg.namespace == "simplemdg/event/core-2"

    def test_empty_namespace_allowed_by_default(self):
        # Historical behaviour preserved: no flag, no boot failure.
        cfg = EventMeshConfig(**_config_kwargs(namespace=""))
        assert cfg.namespace == ""


class _StrictSettings:
    """Minimal settings stub: Event Mesh via env, strict namespace, unset value."""

    MESSAGING_MODE = ""
    INFRA_MODE = "sap"
    EVENT_MESH_CONFIG_SOURCE = "env"
    EVENT_MESH_TOKEN_URL = "https://auth.example.com/oauth/token"
    EVENT_MESH_MESSAGING_URL = "https://messaging.example.com"
    EVENT_MESH_MANAGEMENT_URL = "https://management.example.com"
    EVENT_MESH_CLIENT_ID = "test-id"
    EVENT_MESH_CLIENT_SECRET = "test-secret"
    EVENT_MESH_NAMESPACE = ""
    EVENT_MESH_REQUIRE_NAMESPACE = True
    EVENT_MESH_ALLOW_NAMESPACE_SELF_HEAL = True


class TestBootFailsFast:
    def test_factory_reraises_config_error_instead_of_mock_fallback(self):
        """The generic except must NOT swallow the strict-namespace failure —
        a prod pod silently running on the mock broker is the worst outcome."""
        with pytest.raises(EventMeshConfigError):
            _create_message_broker_client(_StrictSettings())

    def test_factory_still_boots_without_strict_flag(self):
        settings = _StrictSettings()
        settings.EVENT_MESH_REQUIRE_NAMESPACE = False
        client = _create_message_broker_client(settings)
        try:
            assert isinstance(client, EventMeshBrokerClient)
        finally:
            if client is not None:
                client.close()


class TestSelfHealFlag:
    def test_disabled_self_heal_never_mutates_namespace(self):
        names = EventMeshNames("", allow_self_heal=False)
        assert names.adopt_namespace_from_error(_NS_403_BODY) is False
        assert names.namespace == ""
        assert names.qualify_topic("run.started") == "run.started"

    def test_client_wires_flag_from_config(self):
        cfg = EventMeshConfig(
            **_config_kwargs(namespace="", allow_namespace_self_heal=False)
        )
        client = EventMeshBrokerClient(cfg)
        try:
            assert client._names.adopt_namespace_from_error(_NS_403_BODY) is False
            assert client._names.namespace == ""
        finally:
            client.close()


class TestConcurrentAdoption:
    def test_simultaneous_403s_adopt_exactly_once(self):
        names = EventMeshNames("")
        n_threads = 32
        barrier = threading.Barrier(n_threads)
        results: list[bool] = []
        results_lock = threading.Lock()

        def adopt():
            barrier.wait()  # maximize the race window
            outcome = names.adopt_namespace_from_error(_NS_403_BODY)
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=adopt) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(results) == n_threads
        assert results.count(True) == 1  # exactly one winner
        assert names.namespace == _ADOPTED  # no torn/interleaved value
        # Every thread's subsequent qualification uses the adopted namespace.
        assert names.qualify_queue("sub-x") == f"{_ADOPTED}/sub-x"

    def test_adoption_is_idempotent_after_the_race(self):
        names = EventMeshNames("")
        assert names.adopt_namespace_from_error(_NS_403_BODY) is True
        assert names.adopt_namespace_from_error(_NS_403_BODY) is False
        assert names.namespace == _ADOPTED


def _http_error(status: int, text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        str(status), request=MagicMock(), response=resp,
    )
    return resp


class TestLoserThreadStillRetries:
    def test_create_queue_retries_when_namespace_was_adopted_concurrently(self):
        """The in-flight race: our unqualified PUT is on the wire while a
        concurrent caller adopts the namespace. Our 403 handler's adopt()
        then returns False (already adopted) — but the retry must still
        happen, now qualified, instead of 'skipped — continuing'."""
        cfg = EventMeshConfig(**_config_kwargs(namespace=""))
        client = EventMeshBrokerClient(cfg)
        ok = MagicMock()
        ok.raise_for_status = MagicMock()
        put_urls: list[str] = []

        def fake_put(url, **_kw):
            put_urls.append(url)
            if len(put_urls) == 1:
                # Simulate the WINNER thread adopting while our PUT is in flight.
                client._names.adopt_namespace_from_error(_NS_403_BODY)
                return _http_error(403, _NS_403_BODY)
            return ok

        try:
            with patch.object(client._tokens, "ensure", return_value="tok"), \
                 patch.object(client._http, "put", side_effect=fake_put):
                client.create_queue("sub-orchestration-actions")

            assert len(put_urls) == 2  # loser still retried
            assert "simplemdg%2Fevent%2Fobject-9%2Fsub-orchestration-actions" in put_urls[1]
        finally:
            client.close()

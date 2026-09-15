"""Regression tests for the Event Mesh 403 startup crash.

Reproduces the reported failure: queue names reached the shared management
backend unqualified (empty namespace) and were 403-rejected with
"... is not in the namespace=<ns> of the service instance ...", crashing
``subscribe_to_topic`` during lifespan. The broker now self-heals the
namespace from that body and tolerates residual 403/409 instead of crashing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig


def _make_client(**overrides) -> EventMeshBrokerClient:
    monitor = overrides.pop("monitor", None)
    defaults = dict(
        token_url="https://auth.example.com/oauth/token",
        messaging_url="https://messaging.example.com",
        management_url="https://management.example.com",
        client_id="test-id",
        client_secret="test-secret",
        namespace="simplemdg/event/core-2",
    )
    defaults.update(overrides)
    return EventMeshBrokerClient(EventMeshConfig(**defaults), monitor=monitor)


def _http_error(status: int, text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        str(status), request=MagicMock(), response=resp,
    )
    return resp


_NS_403_BODY = (
    "The queue=sub-orchestration-actions is not in the "
    "namespace=simplemdg/event/object-9 of the service instance "
    "id=89bf91ff-e2b0-40ef-ae98-3d03efea2210"
)


class TestNamespaceSelfHeal:
    def test_create_queue_adopts_namespace_and_retries_qualified(self):
        client = _make_client(namespace="")  # unset → names go out unqualified
        ok = MagicMock()
        ok.raise_for_status = MagicMock()
        put_urls: list[str] = []

        def fake_put(url, **_kw):
            put_urls.append(url)
            return _http_error(403, _NS_403_BODY) if len(put_urls) == 1 else ok

        with patch.object(client._tokens, "ensure", return_value="tok"), \
             patch.object(client._http, "put", side_effect=fake_put):
            client.create_queue("sub-orchestration-actions")

        assert client._names.namespace == "simplemdg/event/object-9"
        assert len(put_urls) == 2  # original + one retry
        # Retry targets the namespace-qualified (URL-encoded) queue name.
        assert "simplemdg%2Fevent%2Fobject-9%2Fsub-orchestration-actions" in put_urls[1]

    def test_adopted_namespace_qualifies_subsequent_names(self):
        client = _make_client(namespace="")
        client._names.adopt_namespace_from_error(_NS_403_BODY)
        assert client._names.namespace == "simplemdg/event/object-9"
        assert client._names.qualify_topic("run.started") == "simplemdg/event/object-9/run.started"
        assert (
            client._names.qualify_queue("sub-x") == "simplemdg/event/object-9/sub-x"
        )

    def test_no_reheal_when_namespace_matches(self):
        client = _make_client(namespace="simplemdg/event/object-9")
        assert client._names.adopt_namespace_from_error(_NS_403_BODY) is False


class TestProvisioningTolerates403409:
    def test_create_queue_tolerates_plain_403(self):
        client = _make_client(namespace="simplemdg/event/object-9")
        with patch.object(client._tokens, "ensure", return_value="tok"), \
             patch.object(client._http, "put", return_value=_http_error(403, "Forbidden")):
            client.create_queue("sub-x")  # must not raise

    def test_create_queue_tolerates_409_conflict(self):
        client = _make_client(namespace="simplemdg/event/object-9")
        with patch.object(client._tokens, "ensure", return_value="tok"), \
             patch.object(client._http, "put", return_value=_http_error(409, "exists")):
            client.create_queue("sub-x")  # must not raise

    def test_create_queue_still_raises_on_422(self):
        client = _make_client(namespace="simplemdg/event/object-9")
        with patch.object(client._tokens, "ensure", return_value="tok"), \
             patch.object(client._http, "put",
                          return_value=_http_error(422, "QUOTA_EXCEEDED")):
            with pytest.raises(httpx.HTTPStatusError):
                client.create_queue("sub-x")


class TestSubscribeDoesNotCrash:
    def test_subscribe_survives_persistent_403(self):
        """The reported crash: subscribe_to_topic must not propagate a 403."""
        client = _make_client(
            namespace="simplemdg/event/object-9", consume_transport="rest",
        )
        client._http = MagicMock()
        client._http.get.return_value = MagicMock(status_code=403)  # not-exists
        client._http.put.return_value = _http_error(403, "Forbidden")

        with patch.object(client._tokens, "ensure", return_value="tok"), \
             patch.object(client._consumer, "_poll_loop"):
            client.subscribe_to_topic("orchestration.actions", lambda m: None)  # no raise

        qkey = client._names.qualify_queue(
            client._names.queue_local_from_topic("orchestration.actions")
        )
        assert qkey in client._consumer._callbacks  # callback still registered
        assert client._consumer._consumer_threads  # REST consume still started
        client.close()

    def test_subscribe_self_heals_then_uses_qualified_callback_key(self):
        client = _make_client(namespace="", consume_transport="rest")
        ok = MagicMock()
        ok.raise_for_status = MagicMock()
        put_calls: list[str] = []

        def fake_put(url, **_kw):
            put_calls.append(url)
            # First create_queue PUT 403s (unqualified) then succeeds on retry.
            return _http_error(403, _NS_403_BODY) if len(put_calls) == 1 else ok

        client._http = MagicMock()
        client._http.get.return_value = MagicMock(status_code=404)  # not-exists
        client._http.put.side_effect = fake_put

        with patch.object(client._tokens, "ensure", return_value="tok"), \
             patch.object(client._consumer, "_poll_loop"):
            client.subscribe_to_topic("orchestration.actions", lambda m: None)

        assert client._names.namespace == "simplemdg/event/object-9"
        # Callback keyed by the corrected, namespace-qualified queue name.
        assert "simplemdg/event/object-9/sub-orchestration-actions" in client._consumer._callbacks
        client.close()


_SLEEP = "app.layer4_frameworks.messaging.event_mesh_admin.time.sleep"


class TestManagementGetRetry:
    """queue_exists/subscription_exists retry a transient DNS/socket blip
    quietly instead of logging an ERROR traceback (the reported noise)."""

    def test_queue_exists_retries_transient_then_succeeds(self):
        client = _make_client(namespace="simplemdg/event/object-9")
        ok = MagicMock(status_code=200)
        calls: list[str] = []

        def fake_get(url, **_kw):
            calls.append(url)
            if len(calls) == 1:
                raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")
            return ok

        with patch.object(client._tokens, "ensure", return_value="tok"), \
             patch.object(client._http, "get", side_effect=fake_get), \
             patch(_SLEEP):
            assert client.queue_exists("sub-x") is True
        assert len(calls) == 2  # retried once after the transient failure

    def test_queue_exists_unreachable_returns_false_without_error_log(self, caplog):
        import logging
        client = _make_client(namespace="simplemdg/event/object-9")
        with patch.object(client._tokens, "ensure", return_value="tok"), \
             patch.object(client._http, "get",
                          side_effect=httpx.ConnectError("getaddrinfo failed")), \
             patch(_SLEEP):
            caplog.set_level(logging.WARNING)
            assert client.queue_exists("sub-x") is False
        # Concise WARNING, never an ERROR-level traceback.
        assert not [r for r in caplog.records if r.levelname == "ERROR"]
        assert any(r.levelname == "WARNING" for r in caplog.records)

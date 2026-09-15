"""P2 — dead-message queue + redelivery cap on every provisioned queue.

Measured on tenant-1 (2026-08-05): every WCP queue carries
``maxRedeliveryCount = 0`` and ``deadMsgQueue = None``. SAP's own guidance is
that 0/empty means INDEFINITE, IMMEDIATE redelivery. Under REST polling that
only wastes polls; under AMQP push — where a failed handler settles the
delivery as ``RELEASED`` — one poison message re-loops at full speed forever.

Also measured the same day: the management PUT is a true upsert (201 create /
200 update, 5 -> 9 on an existing queue), so the policy converges onto queues
that already exist. That matters because the alternative the old code comment
recommended — delete + recreate — silently drops every message published while
the topic has no subscribed queue (5 published into the gap, 0 arrived, and the
publisher still got HTTP 204).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig

NAMESPACE = "simplemdg/event/core-2"
QUEUE = "sub-orchestration-actions"
QUALIFIED = f"{NAMESPACE}/{QUEUE}"


def _make_client(**overrides) -> EventMeshBrokerClient:
    defaults = dict(
        token_url="https://auth.example.com/oauth/token",
        messaging_url="https://messaging.example.com",
        management_url="https://management.example.com",
        client_id="test-id",
        client_secret="test-secret",
        namespace=NAMESPACE,
    )
    defaults.update(overrides)
    return EventMeshBrokerClient(EventMeshConfig(**defaults))


def _capture_puts(client, *, queue_exists: bool = False):
    """Run admin calls against a mocked management API; return the PUT bodies."""
    puts: list[tuple[str, dict]] = []
    ok = MagicMock()
    ok.raise_for_status = MagicMock()

    def fake_put(url, **kw):
        puts.append((url, kw.get("json") or {}))
        return ok

    get_resp = MagicMock()
    get_resp.status_code = 200 if queue_exists else 404

    return puts, patch.multiple(
        client._http,
        put=MagicMock(side_effect=fake_put),
        get=MagicMock(return_value=get_resp),
    ), patch.object(client._tokens, "ensure", return_value="tok")


class TestQueueBody:
    def test_queue_gets_a_redelivery_cap_and_a_dmq(self):
        client = _make_client()
        puts, http_patch, tok_patch = _capture_puts(client)
        with http_patch, tok_patch:
            client._admin.create_queue(QUEUE)

        assert len(puts) == 1
        _url, body = puts[0]
        assert body["maxRedeliveryCount"] == 5
        assert body["deadMsgQueue"] == f"{QUALIFIED}-dmq"
        # The lock TTL must survive the new body.
        assert body["messageLockExpirationInSeconds"] == 60

    def test_the_dmq_itself_gets_no_dmq_and_no_cap(self):
        """Otherwise a message failing inside the DMQ needs a DMQ of its own."""
        client = _make_client()
        puts, http_patch, tok_patch = _capture_puts(client)
        with http_patch, tok_patch:
            client._admin.create_queue(f"{QUEUE}-dmq")

        _url, body = puts[0]
        assert "deadMsgQueue" not in body
        assert "maxRedeliveryCount" not in body

    def test_disabling_the_dmq_restores_the_legacy_body(self):
        """A cap without a DMQ DISCARDS at the cap — so they turn off together."""
        client = _make_client(dmq_enabled=False)
        puts, http_patch, tok_patch = _capture_puts(client)
        with http_patch, tok_patch:
            client._admin.create_queue(QUEUE)

        _url, body = puts[0]
        assert body == {"messageLockExpirationInSeconds": 60}

    def test_the_body_never_carries_fields_we_do_not_own(self):
        """A PUT updates exactly what it sends; sending more would clobber."""
        client = _make_client()
        puts, http_patch, tok_patch = _capture_puts(client)
        with http_patch, tok_patch:
            client._admin.create_queue(QUEUE)

        _url, body = puts[0]
        for owned_by_the_broker in ("respectTtl", "maxTtl", "accessType",
                                    "maxQueueSizeInBytes",
                                    "maxDeliveredUnackedMsgsPerFlow"):
            assert owned_by_the_broker not in body


class TestEnsureQueue:
    def test_the_dmq_is_created_before_the_queue_that_references_it(self):
        """A dangling deadMsgQueue is the publisher-DLQ 404 shape all over."""
        client = _make_client()
        puts, http_patch, tok_patch = _capture_puts(client)
        with http_patch, tok_patch:
            client._admin.ensure_queue(QUALIFIED)

        urls = [u for u, _b in puts]
        assert len(urls) == 2
        assert "-dmq" in urls[0], "the DMQ must be provisioned first"
        assert "-dmq" not in urls[1]

    def test_an_existing_queue_is_still_put_so_the_policy_converges(self):
        """The whole point: retrofit live queues WITHOUT delete + recreate.

        The management PUT upserts (verified live), so queues provisioned
        before this policy existed pick it up on the next start. Skipping the
        PUT when the queue exists is what would force a delete cycle — and a
        delete cycle drops everything published into the gap.
        """
        client = _make_client()
        puts, http_patch, tok_patch = _capture_puts(client, queue_exists=True)
        with http_patch, tok_patch:
            client._admin.ensure_queue(QUALIFIED)

        main = [b for u, b in puts if "-dmq" not in u]
        assert main, "an already-existing queue must still be PUT"
        assert main[0]["maxRedeliveryCount"] == 5
        assert main[0]["deadMsgQueue"] == f"{QUALIFIED}-dmq"

    def test_an_existing_dmq_is_not_recreated(self):
        client = _make_client()
        puts, http_patch, tok_patch = _capture_puts(client, queue_exists=True)
        with http_patch, tok_patch:
            client._admin.ensure_queue(QUALIFIED)

        assert not [u for u, _b in puts if "-dmq" in u]

    def test_provision_subscription_routes_through_ensure_queue(self):
        client = _make_client()
        with patch.object(client._admin, "ensure_queue") as ensure, \
             patch.object(client._admin, "subscription_exists", return_value=True):
            client._admin.provision_subscription(QUEUE, "orchestration.actions")

        ensure.assert_called_once_with(QUALIFIED)


class TestStartupSafety:
    """The convergence PUT is new on a path that used to do nothing when the
    queue already existed. It must not turn a management-API blip into a dead
    pod, and it must never leave a queue pointing at a DMQ that isn't there.
    """

    def test_a_failed_converge_on_an_existing_queue_does_not_crash_startup(self):
        client = _make_client()
        with patch.object(client._admin, "queue_exists", return_value=True), \
             patch.object(client._admin, "create_queue",
                          side_effect=RuntimeError("management API blip")):
            client._admin.ensure_queue(QUALIFIED)  # must not raise

    def test_a_failed_FIRST_creation_still_surfaces(self):
        """Nothing to consume from — this one has to be loud."""
        client = _make_client()
        with patch.object(client._admin, "queue_exists", return_value=False), \
             patch.object(client._admin, "create_queue",
                          side_effect=RuntimeError("quota exceeded")):
            with pytest.raises(RuntimeError):
                client._admin.ensure_queue(QUALIFIED)

    def test_an_unprovisionable_dmq_drops_the_cap_instead_of_dangling(self):
        """Unbounded redelivery is bad; dead-lettering into a queue that does
        not exist is worse — it is the publisher-DLQ 404 shape.
        """
        client = _make_client()
        calls: list[tuple[str, dict]] = []

        def fake_create(name, **kw):
            if name.endswith("-dmq"):
                raise RuntimeError("cannot create the DMQ")
            calls.append((name, kw))

        with patch.object(client._admin, "queue_exists", return_value=False), \
             patch.object(client._admin, "create_queue", side_effect=fake_create):
            client._admin.ensure_queue(QUALIFIED)  # degrades, does not fail

        # The queue is still provisioned — just without the dangling reference.
        assert calls == [(QUALIFIED, {"with_dmq": False})]

    def test_with_dmq_false_strips_both_fields_from_the_body(self):
        client = _make_client()
        puts, http_patch, tok_patch = _capture_puts(client)
        with http_patch, tok_patch:
            client._admin.create_queue(QUEUE, with_dmq=False)

        _url, body = puts[0]
        assert body == {"messageLockExpirationInSeconds": 60}


class TestConfigClamp:
    def test_a_dmq_with_a_zero_cap_is_clamped(self):
        """cap=0 + DMQ is the worst case: the cap never trips, the DMQ is
        never used, and the poison loop survives while looking configured."""
        cfg = EventMeshConfig(
            token_url="u", messaging_url="m", management_url="g",
            client_id="i", client_secret="s",
            dmq_enabled=True, max_redelivery_count=0,
        )
        assert cfg.max_redelivery_count == 1

    def test_the_cap_is_left_alone_when_the_dmq_is_off(self):
        cfg = EventMeshConfig(
            token_url="u", messaging_url="m", management_url="g",
            client_id="i", client_secret="s",
            dmq_enabled=False, max_redelivery_count=0,
        )
        assert cfg.max_redelivery_count == 0

    @pytest.mark.parametrize("suffix", ["", None])
    def test_an_empty_suffix_falls_back_to_the_default(self, suffix):
        cfg = EventMeshConfig(
            token_url="u", messaging_url="m", management_url="g",
            client_id="i", client_secret="s",
            dmq_suffix=suffix,
        )
        assert cfg.dmq_suffix == "-dmq"

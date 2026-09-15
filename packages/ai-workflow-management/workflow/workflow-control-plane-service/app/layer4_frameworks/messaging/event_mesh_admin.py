"""SAP Event Mesh management-API client (queues + subscriptions).

Event Mesh has no stand-alone topic object — messages reach a consumer only
through a queue that subscribes to the topic. This component owns that
provisioning surface: queue/subscription create/delete/exists, plus the
idempotent ``provision_subscription`` used by both ``ensure_topic`` and the
consumer's ``subscribe``. Self-heals a missing namespace from a 403 (via the
shared ``EventMeshNames``) and tolerates 403/409 so startup never crashes.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from app.layer4_frameworks.messaging.event_mesh_http import EventMeshHttpClients
from app.layer4_frameworks.messaging.event_mesh_names import EventMeshNames
from app.layer4_frameworks.messaging.event_mesh_token_provider import (
    EventMeshTokenProvider,
)

logger = logging.getLogger(__name__)

# Management-API existence checks (queue_exists/subscription_exists) retry a
# transient connection blip (e.g. a momentary DNS getaddrinfo failure) instead
# of logging a full traceback and falling through to a redundant create. The
# idempotent PUT that follows would recover anyway; this keeps startup quiet
# and the check accurate.
_MANAGEMENT_GET_ATTEMPTS = 3
_MANAGEMENT_GET_RETRY_DELAY_S = 0.3


class EventMeshAdmin:
    """Queue/subscription provisioning over the Event Mesh management API."""

    def __init__(
        self,
        *,
        management_url: str,
        names: EventMeshNames,
        tokens: EventMeshTokenProvider,
        clients: EventMeshHttpClients,
        lock_duration_ms: int,
        dmq_enabled: bool = True,
        dmq_suffix: str = "-dmq",
        max_redelivery_count: int = 5,
        consume_transport: str = "rest",
    ) -> None:
        self._management_url = management_url
        self._names = names
        self._tokens = tokens
        self._clients = clients
        self._lock_duration_ms = lock_duration_ms
        self._dmq_enabled = dmq_enabled
        self._dmq_suffix = dmq_suffix or "-dmq"
        self._max_redelivery_count = max_redelivery_count
        # The queue a topic maps to depends on the consume transport (P1), so
        # ``ensure_topic`` must derive the SAME name the consumer will attach
        # to. Pre-creating the REST-named queue while the consumer subscribes
        # to the AMQP-named one would leave the pre-created queue collecting
        # messages nobody reads.
        self._consume_transport = consume_transport

    @property
    def _http(self) -> httpx.Client:
        return self._clients.sync

    # ------------------------------------------------------------------
    # Dead-message queue naming + queue body
    # ------------------------------------------------------------------

    def is_dmq(self, queue_name: str) -> bool:
        """True for a dead-message queue — it must never get a DMQ of its own.

        Public because callers outside provisioning need it too: `dmq_name`
        APPENDS the suffix, so `dmq_name(q) == q` never holds and cannot be
        used as the test.
        """
        return queue_name.endswith(self._dmq_suffix)

    def _is_dmq(self, queue_name: str) -> bool:
        return self.is_dmq(queue_name)

    def dmq_name(self, queue_name: str) -> str:
        """Qualified name of the DMQ that backs *queue_name*."""
        return self._names.qualify_queue(queue_name) + self._dmq_suffix

    def _queue_body(self, qualified: str, with_dmq: Optional[bool] = None) -> dict:
        """Management-API body for a queue.

        Only the fields we deliberately own are sent — a PUT updates exactly
        what it carries, so omitting ``respectTtl``/``accessType``/sizes leaves
        the broker's values untouched (verified against the live API).
        """
        # Queue-level message lock TTL, so even consumers that don't pass
        # ``x-lock-duration-in-ms`` get the longer window.
        body: dict = {
            "messageLockExpirationInSeconds": max(1, self._lock_duration_ms // 1000),
        }
        # A DMQ takes no redelivery cap and no DMQ of its own — otherwise a
        # message failing in the DMQ would need somewhere else to go.
        # ``with_dmq=None`` means "decide from config"; ``False`` is the caller
        # telling us the DMQ could not be provisioned.
        if with_dmq is None:
            with_dmq = self._dmq_enabled and not self._is_dmq(qualified)
        if with_dmq:
            body["maxRedeliveryCount"] = self._max_redelivery_count
            body["deadMsgQueue"] = self.dmq_name(qualified)
        return body

    # ------------------------------------------------------------------
    # Provisioning (idempotent queue + subscription)
    # ------------------------------------------------------------------

    def ensure_topic(self, topic: str) -> None:
        """Pre-create the queue + subscription pair for *topic*.

        Performs the same idempotent setup ``subscribe`` does implicitly, so
        callers can ensure the routing path exists before any subscriber is
        attached (symmetric with ``KafkaBrokerClient.ensure_topic``).
        """
        self.provision_subscription(self.queue_local_for(topic), topic)

    def queue_local_for(self, topic: str) -> str:
        """The queue local-name *this* client consumes *topic* from.

        Single source of truth for the transport-scoped derivation, so
        provisioning, subscribing and deletion can never disagree.
        """
        return self._names.queue_local_from_topic(
            topic, transport=self._consume_transport,
        )

    def provision_subscription(self, local_queue: str, topic: str) -> None:
        """Idempotently ensure the queue + subscription for *topic* exist.

        Best-effort and startup-safe: ``create_queue`` self-heals a missing
        namespace and tolerates 403/409, and a residual 403/409 on the
        subscription (it may already exist in this or another instance) is
        logged rather than raised. Names are re-qualified between steps because
        ``create_queue`` may adopt the instance namespace from a 403.
        """
        self.ensure_queue(self._names.qualify_queue(local_queue))

        # Re-qualify: the namespace may have just been adopted by create_queue.
        queue_name = self._names.qualify_queue(local_queue)
        qualified_topic = self._names.qualify_topic(topic)
        if self.subscription_exists(queue_name, qualified_topic):
            return
        try:
            self.create_subscription(queue_name, qualified_topic)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (403, 409):
                raise
            logger.warning(
                "Event Mesh subscription %s -> %s not provisioned (status=%d) — "
                "continuing; it may already exist in this/another instance",
                queue_name, qualified_topic, exc.response.status_code,
            )

    def ensure_queue(self, queue_name: str) -> None:
        """Ensure *queue_name* exists, has a DMQ, and carries the redelivery cap.

        The management PUT is a true upsert: it creates a missing queue (201)
        **and updates an existing one** (200), changing exactly the fields the
        body carries. Verified against the live API on 2026-08-05 —
        ``maxRedeliveryCount`` 5 -> 9 on an existing queue. So this converges
        queues provisioned before the DMQ policy existed **without deleting
        them**, which matters: a delete/re-create cycle drops every message
        published while no queue is subscribed to the topic, and the publisher
        still gets HTTP 204 (measured — 5 published into the gap, 0 arrived).
        """
        qualified = self._names.qualify_queue(queue_name)
        # The DMQ must exist before it can be referenced as ``deadMsgQueue``;
        # a dangling reference is the same failure shape as the publisher DLQ
        # that nothing provisions (404 on every dead-letter). If it can't be
        # provisioned, provision the queue WITHOUT the cap rather than point it
        # at a queue that isn't there — unbounded redelivery is bad, but
        # dead-lettering into the void is worse.
        with_dmq = self._dmq_enabled and not self._is_dmq(qualified)
        if with_dmq and not self._ensure_dmq(qualified):
            logger.warning(
                "Event Mesh DMQ %s unavailable — provisioning %s without a "
                "redelivery cap rather than referencing a queue that does not "
                "exist",
                self.dmq_name(qualified), qualified,
            )
            with_dmq = False

        existed = self.queue_exists(qualified)
        try:
            self.create_queue(qualified, with_dmq=with_dmq)
        except Exception:
            # A first creation failing is fatal — there is nothing to consume
            # from. Converging an EXISTING queue is not: the queue is already
            # usable, only the policy update missed, and startup must not die
            # on a transient management-API blip that never used to be on this
            # path at all.
            if not existed:
                raise
            logger.warning(
                "Event Mesh queue %s exists but its redelivery policy could "
                "not be converged this start — continuing", qualified,
            )

    def _ensure_dmq(self, qualified: str) -> bool:
        """Best-effort provision of the DMQ backing *qualified*. True if usable."""
        dmq = self.dmq_name(qualified)
        try:
            if not self.queue_exists(dmq):
                self.create_queue(dmq)
            return True
        except Exception:
            logger.exception("Event Mesh DMQ provisioning failed (queue=%s)", dmq)
            return False

    # ------------------------------------------------------------------
    # Management API
    # ------------------------------------------------------------------

    def _management_get(self, url: str) -> Optional[httpx.Response]:
        """GET a management endpoint, retrying transient connection blips.

        Returns the response (any status code) or ``None`` if the endpoint
        stayed unreachable after ``_MANAGEMENT_GET_ATTEMPTS``. Existence checks
        degrade to "unknown" (treated as not-present) on a concise WARNING
        rather than a full ERROR traceback — a momentary DNS/socket hiccup
        shouldn't look like a hard failure when the idempotent create that
        follows recovers it.
        """
        last_exc: Exception | None = None
        for attempt in range(_MANAGEMENT_GET_ATTEMPTS):
            try:
                return self._http.get(url, headers=self._tokens.auth_headers())
            except httpx.TransportError as exc:  # ConnectError/timeout/etc — transient
                last_exc = exc
                if attempt < _MANAGEMENT_GET_ATTEMPTS - 1:
                    time.sleep(_MANAGEMENT_GET_RETRY_DELAY_S)
            except Exception as exc:  # unexpected — don't retry, don't raise
                logger.warning("Event Mesh management GET failed (%s): %s", url, exc)
                return None
        logger.warning(
            "Event Mesh management endpoint unreachable after %d attempts (%s): %s",
            _MANAGEMENT_GET_ATTEMPTS, url, last_exc,
        )
        return None

    def get_queue(self, queue_name: str) -> Optional[dict]:
        """Full management-API record for *queue_name*, or None if unreadable."""
        qualified = self._names.qualify_queue(queue_name)
        encoded = self._names.encode_segment(qualified)
        url = f"{self._management_url}/hub/rest/api/v1/management/messaging/queues/{encoded}"
        resp = self._management_get(url)
        if resp is None or resp.status_code != 200:
            return None
        try:
            body = resp.json()
        except Exception:
            return None
        return body if isinstance(body, dict) else None

    def consumer_count(self, queue_name: str) -> Optional[int]:
        """Consumers currently bound to *queue_name*; None if unknown.

        This is a LIVE count, not a configured limit — verified 2026-08-05: a
        queue nothing had ever consumed from read 0, while every REST-polled
        production queue read 3 (SAP allocates 3 flows per REST-polled queue).
        """
        body = self.get_queue(queue_name)
        if body is None:
            return None
        value = body.get("consumerCount")
        return int(value) if isinstance(value, (int, float)) else None

    def queue_depth(self, queue_name: str) -> Optional[int]:
        """Messages spooled on *queue_name*; None if unknown.

        Half of the AMQP liveness signal: a link that has delivered nothing is
        only stalled if the broker actually has something to give it.
        """
        body = self.get_queue(queue_name)
        if body is None:
            return None
        value = body.get("messageCount")
        return int(value) if isinstance(value, (int, float)) else None

    def queue_exists(self, queue_name: str) -> bool:
        qualified = self._names.qualify_queue(queue_name)
        encoded = self._names.encode_segment(qualified)
        url = f"{self._management_url}/hub/rest/api/v1/management/messaging/queues/{encoded}"
        resp = self._management_get(url)
        return resp is not None and resp.status_code == 200

    def create_queue(
        self,
        queue_name: str,
        *,
        with_dmq: Optional[bool] = None,
        _allow_namespace_heal: bool = True,
    ) -> None:
        qualified = self._names.qualify_queue(queue_name)
        encoded = self._names.encode_segment(qualified)
        url = f"{self._management_url}/hub/rest/api/v1/management/messaging/queues/{encoded}"
        # This PUT is an upsert: 201 creates, 200 updates an existing queue,
        # changing exactly the fields the body carries (verified live
        # 2026-08-05). The previous note here claimed existing queues were
        # never modified and pointed operators at delete + recreate — that is
        # wrong, and delete + recreate silently drops everything published
        # while the topic has no subscribed queue.
        body = self._queue_body(qualified, with_dmq)
        headers = {**self._tokens.auth_headers(), "Content-Type": "application/json"}
        try:
            resp = self._http.put(url, headers=headers, json=body)
            resp.raise_for_status()
            logger.info(
                "Provisioned queue %s (%s)",
                qualified,
                ", ".join(f"{k}={v}" for k, v in body.items()),
            )
        except httpx.HTTPStatusError as exc:
            # Capture the response body so operators can see the actual
            # Event Mesh reason (quota exceeded, naming policy violation,
            # missing config field, etc.) instead of just the bare 4xx.
            body_snippet = ""
            try:
                body_snippet = exc.response.text[:1000]
            except Exception:
                pass
            status = exc.response.status_code
            # Self-heal: a 403 whose body reveals the instance namespace means
            # the name went out unqualified. Adopt the namespace and retry once
            # with the now-qualified name. Also retry when adoption returns
            # False because a CONCURRENT caller already adopted while our
            # unqualified PUT was in flight — re-qualifying now yields a
            # different name, so the retry is still warranted (SA-1468).
            if status == 403 and _allow_namespace_heal:
                healed = self._names.adopt_namespace_from_error(body_snippet)
                if healed or self._names.qualify_queue(queue_name) != qualified:
                    logger.warning(
                        "Retrying create_queue %s after namespace %s (%r)",
                        queue_name,
                        "adoption" if healed else "was adopted concurrently",
                        self._names.namespace,
                    )
                    self.create_queue(
                        queue_name, with_dmq=with_dmq, _allow_namespace_heal=False,
                    )
                    return
            # A queue that already exists (409) — or one owned by another
            # instance we can't (re)create here (403) — is not fatal: it may
            # already be usable for consume. Don't crash startup over it.
            if status in (403, 409):
                logger.warning(
                    "Event Mesh create_queue %s skipped (status=%d): %s — queue "
                    "may already exist in this/another instance; continuing",
                    qualified, status, body_snippet,
                )
                return
            logger.error(
                "Event Mesh create_queue %s failed: status=%d, body=%r, "
                "qualified=%s, url=%s",
                queue_name, status, body_snippet, qualified, url,
            )
            raise
        except Exception:
            logger.exception("Event Mesh create_queue error (queue=%s)", queue_name)
            raise

    def delete_queue(self, queue_name: str) -> None:
        qualified = self._names.qualify_queue(queue_name)
        encoded = self._names.encode_segment(qualified)
        url = f"{self._management_url}/hub/rest/api/v1/management/messaging/queues/{encoded}"
        try:
            resp = self._http.delete(url, headers=self._tokens.auth_headers())
            resp.raise_for_status()
            logger.info("Deleted queue %s", queue_name)
        except Exception:
            logger.exception("Event Mesh delete_queue error (queue=%s)", queue_name)
            raise

    def list_queues(self) -> list[str]:
        """Return all queues visible to this broker client's credentials.

        Used by the ``tools/cleanup_event_mesh_queues.py`` script to find
        orphan queues from prior deploys. Names are returned in qualified
        form (with namespace prefix). On any error returns an empty list
        — the caller is expected to be human-facing and can retry.
        """
        url = f"{self._management_url}/hub/rest/api/v1/management/messaging/queues"
        try:
            resp = self._http.get(url, headers=self._tokens.auth_headers())
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.exception("Event Mesh list_queues failed")
            return []
        # The API returns either a list of dicts (each with a "name" field)
        # or a wrapper {"queues": [...]} depending on version. Handle both.
        if isinstance(data, dict) and "queues" in data:
            data = data["queues"]
        if not isinstance(data, list):
            return []
        names: list[str] = []
        for item in data:
            if isinstance(item, dict):
                name = item.get("name") or item.get("queueName")
                if isinstance(name, str):
                    names.append(name)
            elif isinstance(item, str):
                names.append(item)
        return names

    def subscription_exists(self, queue_name: str, topic: str) -> bool:
        encoded_queue = self._names.encode_segment(self._names.qualify_queue(queue_name))
        encoded_topic = self._names.encode_segment(self._names.qualify_topic(topic))
        url = (
            f"{self._management_url}/hub/rest/api/v1/management/messaging"
            f"/queues/{encoded_queue}/subscriptions/{encoded_topic}"
        )
        resp = self._management_get(url)
        return resp is not None and resp.status_code == 200

    def create_subscription(self, queue_name: str, topic: str) -> None:
        encoded_queue = self._names.encode_segment(self._names.qualify_queue(queue_name))
        encoded_topic = self._names.encode_segment(self._names.qualify_topic(topic))
        url = (
            f"{self._management_url}/hub/rest/api/v1/management/messaging"
            f"/queues/{encoded_queue}/subscriptions/{encoded_topic}"
        )
        try:
            resp = self._http.put(url, headers=self._tokens.auth_headers())
            resp.raise_for_status()
            logger.info("Created subscription: queue=%s topic=%s", queue_name, topic)
        except Exception:
            logger.exception(
                "Event Mesh create_subscription error (queue=%s, topic=%s)",
                queue_name, topic,
            )
            raise

    def delete_subscription(self, queue_name: str, topic: str) -> None:
        encoded_queue = self._names.encode_segment(self._names.qualify_queue(queue_name))
        encoded_topic = self._names.encode_segment(self._names.qualify_topic(topic))
        url = (
            f"{self._management_url}/hub/rest/api/v1/management/messaging"
            f"/queues/{encoded_queue}/subscriptions/{encoded_topic}"
        )
        try:
            resp = self._http.delete(url, headers=self._tokens.auth_headers())
            resp.raise_for_status()
            logger.info("Deleted subscription: queue=%s topic=%s", queue_name, topic)
        except Exception:
            logger.exception(
                "Event Mesh delete_subscription error (queue=%s, topic=%s)",
                queue_name, topic,
            )
            raise

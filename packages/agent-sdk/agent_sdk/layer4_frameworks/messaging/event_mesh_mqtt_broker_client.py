from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from agent_sdk.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)

logger = logging.getLogger(__name__)


class EventMeshMQTTBrokerClient:
    """SAP Event Mesh MQTT 3.1.1 broker adapter.

    Delivery guarantee note: the underlying ``amqtt`` MQTTClient acknowledges
    QoS-1 messages automatically as soon as they are handed to
    ``deliver_message()`` — there is no manual-ack API in amqtt 0.11.3. As a
    result, if a subscriber callback raises, the message has *already* been
    acknowledged to the broker and cannot be requeued. This transport is
    therefore **best-effort / at-most-once on handler failure**: handler
    exceptions are logged (with the message id) but the message is lost.
    ``ack_message``/``nack_message``/``reject_message`` are intentionally
    no-ops for this reason (see their docstrings).
    """

    # SUBACK return code that indicates the broker refused a subscription.
    _SUBSCRIBE_FAILURE_CODE = 0x80

    def __init__(
        self,
        token_url: str,
        messaging_url: str,
        management_url: str,
        client_id: str,
        client_secret: str,
        namespace: str = "default",
        *,
        messaging_protocol: str = "mqtt311ws",
        verify_ssl: bool = True,
        request_timeout_seconds: float = 30.0,
        auth_mode: str = "oauth2",
        mqtt_client_id: str = "",
        topic_template: str = "{topic_path}",
        queue_template: str = "{queue_path}",
        qos: int = 1,
        keepalive_seconds: int = 60,
        clean_session: bool = True,
        reconnect_retries: int = 3,
        reconnect_max_interval_seconds: int = 10,
        connect_retry_attempts: int = 3,
        connect_retry_initial_delay_seconds: float = 0.2,
        connect_retry_max_delay_seconds: float = 2.0,
        publish_retry_attempts: int = 2,
        publish_retry_initial_delay_seconds: float = 0.2,
        publish_retry_max_delay_seconds: float = 2.0,
        last_will_topic: str = "",
        last_will_message: str = "",
        last_will_qos: int = 1,
        last_will_retain: bool = False,
    ) -> None:
        self._token_url = token_url
        self._messaging_url = messaging_url.rstrip("/")
        self._management_url = management_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._namespace = namespace
        self._messaging_protocol = messaging_protocol.strip().lower()
        self._auth_mode = auth_mode.strip().lower() or "oauth2"
        self._mqtt_client_id = self._normalize_mqtt_client_id(
            mqtt_client_id or client_id or "simplemdg"
        )
        self._topic_template = topic_template or "{topic_path}"
        self._queue_template = queue_template or "{queue_path}"
        self._qos = self._validate_qos(qos, "qos", default=1)
        self._keepalive_seconds = self._validate_non_negative_int(
            keepalive_seconds, "keepalive_seconds", default=60
        )
        self._clean_session = bool(clean_session)
        self._reconnect_retries = self._validate_non_negative_int(
            reconnect_retries, "reconnect_retries", default=3
        )
        self._reconnect_max_interval_seconds = self._validate_positive_int(
            reconnect_max_interval_seconds, "reconnect_max_interval_seconds", default=10
        )
        self._connect_retry_attempts = self._validate_positive_int(
            connect_retry_attempts, "connect_retry_attempts", default=3
        )
        self._connect_retry_initial_delay_seconds = self._validate_non_negative_float(
            connect_retry_initial_delay_seconds,
            "connect_retry_initial_delay_seconds",
            default=0.2,
        )
        self._connect_retry_max_delay_seconds = self._validate_non_negative_float(
            connect_retry_max_delay_seconds,
            "connect_retry_max_delay_seconds",
            default=2.0,
        )
        self._publish_retry_attempts = self._validate_positive_int(
            publish_retry_attempts, "publish_retry_attempts", default=2
        )
        self._publish_retry_initial_delay_seconds = self._validate_non_negative_float(
            publish_retry_initial_delay_seconds,
            "publish_retry_initial_delay_seconds",
            default=0.2,
        )
        self._publish_retry_max_delay_seconds = self._validate_non_negative_float(
            publish_retry_max_delay_seconds,
            "publish_retry_max_delay_seconds",
            default=2.0,
        )
        self._last_will_topic = str(last_will_topic or "").strip()
        self._last_will_message = str(last_will_message or "")
        self._last_will_qos = self._validate_qos(
            last_will_qos, "last_will_qos", default=1
        )
        self._last_will_retain = bool(last_will_retain)
        self._verify_ssl = bool(verify_ssl)
        self._http = httpx.Client(verify=verify_ssl, timeout=request_timeout_seconds)
        self._access_token = ""
        self._token_expires_at = 0.0
        self._callbacks: dict[str, list[Callable]] = {}
        # Topic filters each queue was subscribed with. Required so dispatch can
        # match delivered messages by their original topic — not just the queue
        # address — for subscribe_to_queue(queue_name, topic) where the queue
        # name differs from the topic (the broker delivers on the topic path).
        self._queue_topic_filters: dict[str, set[str]] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._client: Any | None = None
        self._client_lock: asyncio.Lock | None = None
        self._subscribe_lock: asyncio.Lock | None = None
        # Tracks which client instance has had subscriptions applied, and which
        # queues are applied on it. MQTT subscriptions live on the (clean)
        # session, so a reconnect yields a fresh client with none of them; this
        # lets _sync_subscriptions reapply them and add new queues incrementally
        # without ever double-subscribing on the same client.
        self._sub_client: Any | None = None
        self._applied_queues: set[str] = set()
        self._receiver_task: asyncio.Future | None = None
        self._running = False
        self._provisioner: EventMeshBrokerClient | None = None
        if self._management_url:
            self._provisioner = EventMeshBrokerClient(
                token_url=token_url,
                messaging_url=management_url or messaging_url,
                management_url=management_url,
                client_id=client_id,
                client_secret=client_secret,
                namespace=namespace,
                verify_ssl=verify_ssl,
                request_timeout_seconds=request_timeout_seconds,
            )

    @staticmethod
    def _validate_qos(value: Any, field: str, *, default: int) -> int:
        resolved = int(value) if value is not None else default
        if resolved not in (0, 1, 2):
            raise ValueError(
                f"Event Mesh MQTT {field} must be 0, 1, or 2 (got {resolved})"
            )
        return resolved

    @staticmethod
    def _validate_non_negative_int(value: Any, field: str, *, default: int) -> int:
        resolved = int(value) if value is not None else default
        if resolved < 0:
            raise ValueError(f"Event Mesh MQTT {field} must be >= 0 (got {resolved})")
        return resolved

    @staticmethod
    def _validate_positive_int(value: Any, field: str, *, default: int) -> int:
        resolved = int(value) if value is not None else default
        if resolved < 1:
            raise ValueError(f"Event Mesh MQTT {field} must be >= 1 (got {resolved})")
        return resolved

    @staticmethod
    def _validate_non_negative_float(
        value: Any, field: str, *, default: float
    ) -> float:
        resolved = float(value) if value is not None else default
        if resolved < 0:
            raise ValueError(f"Event Mesh MQTT {field} must be >= 0 (got {resolved})")
        return resolved

    @staticmethod
    def _is_retryable_http_exception(exc: Exception) -> bool:
        if isinstance(
            exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)
        ):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            return status == 429 or 500 <= status <= 599
        return False

    @staticmethod
    def _retry_delay_seconds(
        attempt_index: int, *, initial: float, maximum: float
    ) -> float:
        if maximum <= 0 or initial <= 0:
            return 0.0
        base = min(maximum, initial * (2 ** max(attempt_index, 0)))
        jitter = random.uniform(0.0, base * 0.25)
        return min(maximum, base + jitter)

    async def _sleep_before_retry_async(
        self, attempt_index: int, *, initial: float, maximum: float
    ) -> None:
        delay = self._retry_delay_seconds(
            attempt_index,
            initial=initial,
            maximum=maximum,
        )
        if delay > 0:
            await asyncio.sleep(delay)

    def _import_amqtt(self) -> Any:
        try:
            from amqtt.client import MQTTClient
        except ImportError as exc:
            raise RuntimeError(
                "EVENT_MESH_PROTOCOL=mqtt311ws/mqtt311 requires the optional MIT-licensed "
                "dependency amqtt. Install it with: pip install amqtt"
            ) from exc
        return MQTTClient

    def _get_access_token(self, *, force_refresh: bool = False) -> str:
        if self._auth_mode not in {"oauth2", "token", "bearer"}:
            return self._client_secret
        now = time.time()
        if (not force_refresh) and self._access_token and now < self._token_expires_at:
            return self._access_token
        if not self._token_url:
            return self._client_secret

        attempts = max(int(getattr(self, "_connect_retry_attempts", 3)), 1)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._http.post(
                    self._token_url,
                    data={"grant_type": "client_credentials"},
                    auth=(self._client_id, self._client_secret),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                data = response.json()
                token = data.get("access_token", "")
                if not token:
                    raise RuntimeError("Event Mesh token response missing access_token")
                expires_in = int(data.get("expires_in", 300))
                self._access_token = token
                self._token_expires_at = time.time() + max(expires_in - 30, 30)
                return token
            except Exception as exc:
                last_exc = exc
                if attempt >= attempts - 1:
                    break
                if not self._is_retryable_http_exception(exc):
                    break
                logger.debug(
                    "Retrying Event Mesh MQTT OAuth token request attempt=%s",
                    attempt + 1,
                    exc_info=True,
                )
                time.sleep(
                    self._retry_delay_seconds(
                        attempt,
                        initial=self._connect_retry_initial_delay_seconds,
                        maximum=self._connect_retry_max_delay_seconds,
                    )
                )

        assert last_exc is not None
        raise last_exc

    def _build_topic_path(self, subject: str) -> str:
        return f"{self._namespace}/{subject}" if self._namespace else subject

    def _build_queue_name(self, subject: str) -> str:
        return f"{self._namespace}/{subject}" if self._namespace else subject

    def build_queue_name(self, subject: str) -> str:
        return self._build_queue_name(subject)

    def _format_address(
        self, template: str, *, topic: str = "", queue: str = ""
    ) -> str:
        topic_path = self._build_topic_path(topic) if topic else ""
        queue_path = queue or (self._build_queue_name(topic) if topic else "")
        return template.format(
            topic=topic,
            queue=queue,
            namespace=self._namespace,
            topic_path=topic_path,
            queue_path=queue_path,
        )

    def _topic_address(self, topic: str) -> str:
        return self._format_address(self._topic_template, topic=topic)

    def _queue_address(self, queue_name: str) -> str:
        return self._format_address(self._queue_template, queue=queue_name)

    def _normalize_mqtt_uri(self) -> str:
        parsed = urlparse(self._messaging_url)
        if not parsed.scheme:
            scheme = "wss" if "ws" in self._messaging_protocol else "mqtts"
            return f"{scheme}://{self._messaging_url}"
        return self._messaging_url

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None:
            return self._loop
        loop = asyncio.new_event_loop()
        self._loop = loop

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        self._loop_thread = threading.Thread(
            target=_run,
            name="event-mesh-mqtt-loop",
            daemon=True,
        )
        self._loop_thread.start()
        return loop

    def _run_coro_sync(self, coro: Any, *, timeout: float = 30.0) -> Any:
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)

    @staticmethod
    def _normalize_mqtt_client_id(value: str) -> str:
        # Return a SAP/MQTT-3.1.1-safe MQTT client identifier.
        #
        # MQTT 3.1.1 brokers must accept 1-23 byte identifiers containing only
        # letters and digits. Some brokers accept more, but SAP Event Mesh can
        # reject service OAuth client IDs with CONNACK code 2 because those IDs are
        # long and contain punctuation. Keep the public env override, but never
        # send an unsafe identifier to the MQTT broker.
        import hashlib

        raw = str(value or "simplemdg").strip()
        safe = "".join(ch for ch in raw if ch.isalnum())
        if 1 <= len(safe) <= 23:
            return safe

        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return f"sm{digest[:21]}"

    def _mqtt_ws_additional_headers(self) -> dict[str, str]:
        # SAP Event Mesh MQTT-over-WebSocket with OAuth authenticates the
        # WebSocket HTTP Upgrade using Authorization: Bearer <token>.
        #
        # MQTT URI userinfo is still useful for brokers that authenticate at the
        # MQTT CONNECT layer, but SAP returns HTTP 401 before MQTT CONNECT if the
        # WebSocket Upgrade lacks the Bearer header.
        if "ws" not in self._messaging_protocol:
            return {}
        if self._auth_mode not in {"oauth2", "token", "bearer"}:
            return {}

        raw_token = (self._get_access_token() or "").strip()
        if not raw_token:
            return {}

        if raw_token.lower().startswith("bearer "):
            parts = raw_token.split(None, 1)
            bearer_token = parts[1].strip() if len(parts) > 1 else ""
        else:
            bearer_token = raw_token

        # HTTP headers must not contain embedded whitespace/newlines. OAuth JWTs
        # do not need whitespace, so normalize defensively.
        bearer_token = "".join(bearer_token.split())
        if not bearer_token:
            return {}

        return {"Authorization": f"Bearer {bearer_token}"}

    @staticmethod
    def _uri_with_credentials(uri: str, username: str, password: str) -> str:
        # Return uri with MQTT credentials embedded in URI userinfo.
        #
        # aMQTT's MQTTClient.connect() does not accept username/password keyword
        # arguments. It reads MQTT credentials from the URI instead.
        #
        # Values are percent-encoded because OAuth tokens/client secrets can
        # contain reserved URI characters.
        if not username:
            return uri

        from urllib.parse import quote, urlparse, urlunparse

        parsed = urlparse(uri)
        if parsed.username or parsed.password:
            return uri

        hostport = parsed.netloc
        if "@" in hostport:
            hostport = hostport.rsplit("@", 1)[1]

        userinfo = (
            f"{quote(str(username), safe='')}:{quote(str(password or ''), safe='')}"
        )
        return urlunparse(
            (
                parsed.scheme,
                f"{userinfo}@{hostport}",
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

    def _mqtt_client_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "keep_alive": self._keepalive_seconds,
            "ping_delay": max(self._keepalive_seconds // 2, 1),
            "auto_reconnect": True,
            "reconnect_retries": self._reconnect_retries,
            "reconnect_max_interval": self._reconnect_max_interval_seconds,
            "cleansession": self._clean_session,
            "default_qos": self._qos,
            "default_retain": False,
        }
        if self._last_will_topic and self._last_will_message:
            config["will"] = {
                "topic": self._last_will_topic,
                "message": self._last_will_message,
                "qos": self._last_will_qos,
                "retain": self._last_will_retain,
            }
        return config

    async def _reset_client_async(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            logger.debug(
                "Failed to disconnect stale MQTT client cleanly", exc_info=True
            )

    def _get_client_lock(self) -> asyncio.Lock:
        # Lazily create the lock so it binds to the private event loop it is
        # used on. Only ever called from coroutines running on that loop, so
        # creation itself is not racy.
        if self._client_lock is None:
            self._client_lock = asyncio.Lock()
        return self._client_lock

    def _get_subscribe_lock(self) -> asyncio.Lock:
        if self._subscribe_lock is None:
            self._subscribe_lock = asyncio.Lock()
        return self._subscribe_lock

    async def _sync_subscriptions(self, client: Any) -> None:
        """Ensure every registered queue is subscribed on ``client``.

        MQTT subscriptions live on the session, and this transport uses
        ``clean_session``, so a reconnect (after a publish/receive failure)
        produces a fresh client with **no** subscriptions — delivery would
        silently stop. This reapplies all known subscriptions whenever the
        client instance changes, and adds any newly-registered queue
        incrementally. Queues already applied to the current client are skipped,
        so the first ``subscribe_to_queue`` path never double-subscribes.
        """
        async with self._get_subscribe_lock():
            if self._sub_client is not client:
                # New (reconnected) client: nothing is subscribed on it yet.
                self._sub_client = client
                self._applied_queues = set()
            with self._lock:
                pending = [
                    queue_name
                    for queue_name in self._callbacks
                    if queue_name not in self._applied_queues
                ]
            for queue_name in pending:
                address = self._queue_address(queue_name)
                granted = await client.subscribe([(address, self._qos)])
                self._check_subscribe_result(granted, address)
                self._applied_queues.add(queue_name)

    async def _ensure_client_async(self) -> Any:
        if self._client is not None:
            return self._client

        # Guard client creation so two concurrent coroutines (e.g. the
        # receiver loop reconnecting and a publish) cannot both build and
        # connect a client and leak one.
        async with self._get_client_lock():
            if self._client is not None:
                return self._client
            return await self._connect_new_client_async()

    async def _connect_new_client_async(self) -> Any:
        MQTTClient = self._import_amqtt()
        last_exc: Exception | None = None
        for attempt in range(self._connect_retry_attempts):
            client = MQTTClient(
                client_id=self._mqtt_client_id,
                config=self._mqtt_client_config(),
            )
            try:
                password = (
                    self._client_secret
                    if self._auth_mode in {"clientsecret", "client-secret", "basic"}
                    else self._get_access_token(force_refresh=attempt > 0)
                )
                uri = self._normalize_mqtt_uri()
                username = self._client_id or ""
                if not username or not password:
                    raise RuntimeError(
                        "Event Mesh MQTT credentials are incomplete; "
                        "client_id and password/token are required."
                    )
                await client.connect(
                    self._uri_with_credentials(uri, username, password),
                    additional_headers=self._mqtt_ws_additional_headers(),
                )
                self._client = client
                self._running = True
                return client
            except Exception as exc:
                last_exc = exc
                try:
                    await client.disconnect()
                except Exception:
                    pass
                if attempt >= self._connect_retry_attempts - 1:
                    break
                logger.debug(
                    "Retrying Event Mesh MQTT connect attempt=%s/%s",
                    attempt + 1,
                    self._connect_retry_attempts,
                    exc_info=True,
                )
                await self._sleep_before_retry_async(
                    attempt,
                    initial=self._connect_retry_initial_delay_seconds,
                    maximum=self._connect_retry_max_delay_seconds,
                )

        assert last_exc is not None
        raise last_exc

    def _ensure_client(self) -> Any:
        return self._run_coro_sync(self._ensure_client_async())

    async def _invoke_callback(
        self, callback: Callable, payload: Any, headers: dict[str, str], queue_name: str
    ) -> None:
        try:
            inspect.signature(callback).bind(payload, headers, queue_name)
        except (TypeError, ValueError):
            result = callback(payload, headers)
        else:
            result = callback(payload, headers, queue_name)
        if inspect.isawaitable(result):
            await result

    async def _refresh_token_after_failure(self) -> None:
        # After a receive/connection failure, drop the (possibly disconnected
        # or auth-expired) client and pre-refresh the OAuth token so the next
        # _ensure_client_async() rebuilds the client — and, for mqtt311ws,
        # re-injects a fresh Bearer token into the WS Upgrade header. amqtt's
        # auto_reconnect reuses the stale token, so we must rebuild instead.
        await self._reset_client_async()
        if self._auth_mode in {"oauth2", "token", "bearer"}:
            try:
                self._get_access_token(force_refresh=True)
            except Exception:
                logger.debug(
                    "Failed to refresh MQTT OAuth token before receiver reconnect",
                    exc_info=True,
                )

    async def _dispatch_message(self, message: Any) -> None:
        packet = getattr(message, "publish_packet", None)
        if packet is None:
            return
        variable_header = getattr(packet, "variable_header", None)
        topic = str(getattr(variable_header, "topic_name", ""))
        message_id = getattr(variable_header, "packet_id", None)
        raw_payload = getattr(getattr(packet, "payload", None), "data", b"")
        if isinstance(raw_payload, str):
            text = raw_payload
        else:
            text = bytes(raw_payload or b"").decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            payload = text
        with self._lock:
            callbacks_by_queue = list(self._callbacks.items())
            topic_filters_by_queue = {
                queue_name: set(filters)
                for queue_name, filters in self._queue_topic_filters.items()
            }
        for queue_name, callbacks in callbacks_by_queue:
            if not self._topic_matches_subscription(
                topic, queue_name, topic_filters_by_queue.get(queue_name, ())
            ):
                continue
            headers = {"x-agent-sdk-mqtt-topic": topic}
            for callback in callbacks:
                await self._run_callback(
                    callback, payload, headers, queue_name, message_id
                )

    def _topic_matches_subscription(
        self, topic: str, queue_name: str, topic_filters: Any
    ) -> bool:
        # A delivered message belongs to a subscription when its topic equals the
        # queue address (subscribe_to_topic collapses queue address and topic
        # path to the same value) OR it matches one of the topic filters the
        # queue was subscribed with. The topic-filter branch is what makes
        # subscribe_to_queue(queue_name, topic) with queue_name != topic work:
        # the broker delivers on the original topic path, not the queue address.
        if topic == self._queue_address(queue_name):
            return True
        return any(
            self._mqtt_topic_matches(topic_filter, topic)
            for topic_filter in topic_filters
        )

    @staticmethod
    def _mqtt_topic_matches(topic_filter: str, topic: str) -> bool:
        # Standard MQTT 3.1.1 topic-filter matching, including the single-level
        # (``+``) and multi-level (``#``) wildcards, so hierarchical SAP topics
        # subscribed via a queue are matched correctly.
        if topic_filter == topic:
            return True
        filter_parts = topic_filter.split("/")
        topic_parts = topic.split("/")
        for index, part in enumerate(filter_parts):
            if part == "#":
                return True
            if index >= len(topic_parts):
                return False
            if part == "+":
                continue
            if part != topic_parts[index]:
                return False
        return len(filter_parts) == len(topic_parts)

    async def _run_callback(
        self,
        callback: Callable,
        payload: Any,
        headers: dict[str, str],
        queue_name: str,
        message_id: Any = None,
    ) -> None:
        # Mirror EventMeshBrokerClient._run_handler: isolate each callback so a
        # raising handler never kills the receiver loop. The message is already
        # acked by amqtt (at-most-once on failure), so we log the loss instead
        # of silently swallowing it.
        try:
            await self._invoke_callback(callback, payload, headers, queue_name)
        except Exception:
            logger.exception(
                "Error in MQTT callback for queue=%s message_id=%s "
                "(message already acked by broker; not requeued)",
                queue_name,
                message_id,
            )

    async def _receiver_loop(self) -> None:
        while self._running:
            # Re-acquire the current client every iteration so we recover after
            # a publish-triggered reset (the old client is replaced) or an
            # auth/connection failure. Re-apply subscriptions on any fresh
            # client — clean_session means a reconnect drops them, so without
            # this delivery would stop silently after the first reconnect.
            try:
                client = await self._ensure_client_async()
                await self._sync_subscriptions(client)
            except Exception:
                if self._running:
                    logger.exception(
                        "MQTT receiver could not (re)connect or re-subscribe"
                    )
                await self._reset_client_async()
                await asyncio.sleep(1)
                continue
            try:
                message = await asyncio.wait_for(client.deliver_message(), timeout=1)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._running:
                    logger.exception("MQTT receive failed; will reconnect")
                await self._refresh_token_after_failure()
                await asyncio.sleep(1)
                continue
            try:
                await self._dispatch_message(message)
            except Exception:
                logger.exception("Failed to dispatch MQTT message")

    def _on_receiver_done(self, task: Any) -> None:
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is None:
            return
        logger.error("MQTT receiver loop exited unexpectedly", exc_info=exc)
        if self._running:
            logger.info("Restarting MQTT receiver loop")
            self._receiver_task = asyncio.ensure_future(self._receiver_loop())
            self._receiver_task.add_done_callback(self._on_receiver_done)

    def publish_to_topic(
        self, topic: str, message: Any, key: str | None = None
    ) -> None:
        async def _publish() -> None:
            payload = (
                json.dumps(message).encode("utf-8")
                if isinstance(message, (dict, list))
                else str(message).encode("utf-8")
            )
            last_exc: Exception | None = None
            for attempt in range(self._publish_retry_attempts):
                try:
                    client = await self._ensure_client_async()
                    await client.publish(
                        self._topic_address(topic), payload, qos=self._qos
                    )
                    return
                except Exception as exc:
                    last_exc = exc
                    logger.debug(
                        "MQTT publish failed; reconnecting topic=%s attempt=%s/%s",
                        topic,
                        attempt + 1,
                        self._publish_retry_attempts,
                        exc_info=True,
                    )
                    await self._reset_client_async()
                    if self._auth_mode in {"oauth2", "token", "bearer"}:
                        try:
                            self._get_access_token(force_refresh=True)
                        except Exception:
                            logger.debug(
                                "Failed to refresh MQTT OAuth token before publish retry",
                                exc_info=True,
                            )
                    if attempt >= self._publish_retry_attempts - 1:
                        break
                    await self._sleep_before_retry_async(
                        attempt,
                        initial=self._publish_retry_initial_delay_seconds,
                        maximum=self._publish_retry_max_delay_seconds,
                    )
            assert last_exc is not None
            raise last_exc

        self._run_coro_sync(_publish())

    def ensure_queue(self, queue_name: str) -> None:
        if self._provisioner is not None:
            self._provisioner.ensure_queue(queue_name)

    def ensure_queue_subscription(self, queue_name: str, topic_pattern: str) -> None:
        if self._provisioner is not None:
            self._provisioner.ensure_queue_subscription(queue_name, topic_pattern)

    def subscribe_to_topic(self, topic: str, callback: Callable) -> None:
        self.subscribe_to_queue(self._build_queue_name(topic), topic, callback)

    def subscribe_to_topics(
        self,
        topics: list[str] | tuple[str, ...],
        callback: Callable,
        queue_name: str | None = None,
    ) -> None:
        for topic in dict.fromkeys(
            str(item).strip() for item in topics if str(item).strip()
        ):
            self.subscribe_to_queue(
                queue_name or self._build_queue_name(topic), topic, callback
            )

    def subscribe_to_queue(
        self, queue_name: str, topic: str, callback: Callable
    ) -> None:
        topic_pattern = self._build_topic_path(topic)
        with self._lock:
            handlers = self._callbacks.setdefault(queue_name, [])
            if callback not in handlers:
                handlers.append(callback)
            self._queue_topic_filters.setdefault(queue_name, set()).add(topic_pattern)
        try:
            self.ensure_queue(queue_name)
            self.ensure_queue_subscription(queue_name, topic_pattern)
        except Exception:
            logger.exception(
                "Failed to auto-provision Event Mesh queue/subscription (queue=%s, topic=%s)",
                queue_name,
                topic_pattern,
            )

        async def _subscribe() -> None:
            client = await self._ensure_client_async()
            # Apply the full known subscription set (which now includes this
            # queue). _sync_subscriptions skips queues already applied to the
            # current client, so this adds only the new queue when the client
            # is already connected, and recovers everything on a fresh client.
            await self._sync_subscriptions(client)
            if self._receiver_task is None or self._receiver_task.done():
                self._running = True
                self._receiver_task = asyncio.ensure_future(self._receiver_loop())
                self._receiver_task.add_done_callback(self._on_receiver_done)

        self._run_coro_sync(_subscribe())

    @classmethod
    def _check_subscribe_result(cls, granted: Any, address: str) -> None:
        # amqtt's subscribe() returns the per-topic granted QoS codes from the
        # broker's SUBACK. 0x80 means the subscription was refused; without
        # this check we would silently receive no messages for that topic.
        codes = list(granted or [])
        if any(code == cls._SUBSCRIBE_FAILURE_CODE for code in codes):
            raise RuntimeError(
                f"Event Mesh MQTT subscription refused for '{address}' "
                f"(SUBACK returned 0x80); check ACLs/authorizations"
            )

    def ack_message(self, queue_name: str, message_headers: dict[str, str]) -> None:
        # No-op: amqtt (0.11.3) auto-acks QoS-1 messages when they are handed to
        # deliver_message(). There is no manual-ack API, so acking here is both
        # unnecessary and impossible.
        return None

    def nack_message(
        self, queue_name: str, message_headers: dict[str, str], requeue: bool = True
    ) -> None:
        # No-op / cannot requeue: amqtt has no manual nack and has already acked
        # the message by the time a handler runs. This transport is best-effort
        # at-most-once on handler failure; a failed message is lost, not
        # redelivered. Handler exceptions are logged with the message id (see
        # _run_callback) so the loss is visible.
        return None

    def reject_message(
        self, queue_name: str, message_headers: dict[str, str], requeue: bool = False
    ) -> None:
        # No-op / cannot requeue — see nack_message. amqtt provides no way to
        # reject or requeue a delivered message.
        return None

    def close(self) -> None:
        self._running = False

        async def _shutdown() -> None:
            task = self._receiver_task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.debug(
                        "MQTT receiver task raised during shutdown", exc_info=True
                    )
            if self._client is not None:
                await self._client.disconnect()

        try:
            if self._loop is not None:
                self._run_coro_sync(_shutdown(), timeout=10)
        except Exception:
            logger.debug("Failed to disconnect MQTT client cleanly", exc_info=True)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=10)
        self._loop = None
        self._loop_thread = None
        self._client = None
        self._receiver_task = None
        if self._provisioner is not None:
            self._provisioner.close()
        self._http.close()

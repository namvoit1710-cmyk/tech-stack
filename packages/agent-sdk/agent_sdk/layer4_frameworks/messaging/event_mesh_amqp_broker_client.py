from __future__ import annotations

import inspect
import json
import logging
import queue
import random
import socket
import ssl
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from agent_sdk.layer1_domain.value_objects.agent_control import (
    extract_conv_id,
    is_agent_control_message,
)
from agent_sdk.layer4_frameworks.messaging.consumer_dispatch_runtime import (
    ConsumerDispatchRuntime,
)
from agent_sdk.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)

logger = logging.getLogger(__name__)


class _PyAMQPDelivery:
    def __init__(
        self,
        *,
        client: Any,
        frame: Any,
        message: Any,
        settlement_lock: threading.RLock,
        queue_name: str = "",
    ) -> None:
        self._client = client
        self._frame = frame
        self._message = message
        self._settlement_lock = settlement_lock
        self._settled = False
        # Consumer that owns this delivery. Settlement must be marshalled back
        # to that consumer's thread because the vendored AMQP connection/session
        # are not thread-safe (see EventMeshAMQPBrokerClient._perform_settlement).
        self.queue_name = queue_name
        # Number of dispatched callbacks that have not yet completed.
        self.pending = 0
        # True once any callback for this delivery raised.
        self.errored = False
        # True once a settlement has been requested (explicit ack/nack or the
        # auto-settle-on-completion path), so we never enqueue it twice.
        self.settle_requested = False

    def _frame_value(self, name: str, index: int | None = None) -> Any:
        value = getattr(self._frame, name, None)
        if value is not None:
            return value
        if isinstance(self._frame, dict):
            return self._frame.get(name)
        if index is not None:
            try:
                return self._frame[index]
            except Exception:
                return None
        return None

    @staticmethod
    def _coerce_delivery_tag(value: Any) -> bytes | None:
        """Return a bytes delivery-tag for Azure _pyamqp settlement.

        Azure _pyamqp tracks received delivery tags as raw bytes. If a frame
        implementation gives us a memoryview/bytearray/bytes-like object, using
        it directly can fail ReceiverLink's membership check before disposition.
        """
        if value is None:
            return None
        if isinstance(value, bytes):
            return value
        if isinstance(value, bytearray):
            return bytes(value)
        if isinstance(value, memoryview):
            return value.tobytes()
        if isinstance(value, int):
            logger.warning(
                "Unsupported AMQP delivery_tag type for settlement: %s",
                type(value).__name__,
            )
            return None

        tobytes = getattr(value, "tobytes", None)
        if callable(tobytes):
            try:
                converted = tobytes()
            except Exception:
                logger.warning(
                    "Failed to coerce AMQP delivery_tag using tobytes()",
                    exc_info=True,
                )
            else:
                if isinstance(converted, bytes):
                    return converted
                if isinstance(converted, bytearray):
                    return bytes(converted)

        if isinstance(value, str):
            return value.encode("utf-8")

        try:
            return bytes(value)
        except (TypeError, ValueError):
            logger.warning(
                "Unsupported AMQP delivery_tag type for settlement: %s",
                type(value).__name__,
            )
            return None

    @property
    def delivery_id(self) -> Any:
        return self._frame_value("delivery_id", 1)

    @property
    def delivery_tag(self) -> bytes | None:
        return self._coerce_delivery_tag(self._frame_value("delivery_tag", 2))

    def settle(self, outcome: str) -> None:
        if self._settled:
            return
        delivery_id = self.delivery_id
        delivery_tag = self.delivery_tag
        if delivery_id is None or delivery_tag is None:
            logger.debug(
                "Cannot settle AMQP delivery because frame lacks delivery_id/delivery_tag"
            )
            self._settled = True
            return
        with self._settlement_lock:
            self._client.settle_messages(delivery_id, delivery_tag, outcome)
        self._settled = True

    def accept(self) -> None:
        self.settle("accepted")

    def release(self) -> None:
        self.settle("released")

    def reject(self) -> None:
        self.settle("rejected")


class EventMeshAMQPBrokerClient:
    """SAP Event Mesh AMQP 1.0 broker adapter using vendored Azure `_pyamqp`."""

    def __init__(
        self,
        token_url: str,
        messaging_url: str,
        management_url: str,
        client_id: str,
        client_secret: str,
        namespace: str = "default",
        *,
        messaging_protocol: str = "amqp10ws",
        verify_ssl: bool = True,
        request_timeout_seconds: float = 30.0,
        max_in_flight_messages: int = 4,
        shutdown_grace_seconds: int = 30,
        auth_mode: str = "oauth2",
        topic_address_template: str = "topic:{topic_path}",
        queue_address_template: str = "queue:{queue_path}",
        prefetch: int = 10,
        poll_timeout_seconds: float = 1.0,
        default_settlement_outcome: str = "accepted",
        default_error_settlement_outcome: str = "released",
        debug: bool = False,
        token_retry_attempts: int = 3,
        token_retry_initial_delay_seconds: float = 0.2,
        token_retry_max_delay_seconds: float = 2.0,
        publish_retry_attempts: int = 2,
        publish_retry_initial_delay_seconds: float = 0.2,
        publish_retry_max_delay_seconds: float = 2.0,
        send_client_cache_max_size: int = 128,
        send_client_idle_ttl_seconds: float = 900.0,
    ) -> None:
        self._token_url = token_url
        self._messaging_url = messaging_url.rstrip("/")
        self._management_url = management_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._namespace = namespace
        self._messaging_protocol = messaging_protocol.strip().lower()
        self._auth_mode = auth_mode.strip().lower() or "oauth2"
        self._topic_address_template = topic_address_template or "topic:{topic_path}"
        self._queue_address_template = queue_address_template or "queue:{queue_path}"
        self._prefetch = max(int(prefetch or 10), 1)
        self._poll_timeout_seconds = max(float(poll_timeout_seconds or 1.0), 0.1)
        self._default_settlement_outcome = self._normalize_outcome(
            default_settlement_outcome, "accepted"
        )
        self._default_error_settlement_outcome = self._normalize_outcome(
            default_error_settlement_outcome, "released"
        )
        self._debug = bool(debug)
        self._verify_ssl = bool(verify_ssl)
        self._request_timeout_seconds = float(request_timeout_seconds or 30.0)
        self._token_retry_attempts = max(int(token_retry_attempts or 3), 1)
        self._token_retry_initial_delay_seconds = max(
            float(token_retry_initial_delay_seconds or 0.2), 0.0
        )
        self._token_retry_max_delay_seconds = max(
            float(token_retry_max_delay_seconds or 2.0), 0.0
        )
        self._publish_retry_attempts = max(int(publish_retry_attempts or 2), 1)
        self._publish_retry_initial_delay_seconds = max(
            float(publish_retry_initial_delay_seconds or 0.2), 0.0
        )
        self._publish_retry_max_delay_seconds = max(
            float(publish_retry_max_delay_seconds or 2.0), 0.0
        )
        self._http = httpx.Client(verify=verify_ssl, timeout=request_timeout_seconds)
        self._access_token = ""
        self._token_expires_at = 0.0
        self._callbacks: dict[str, list[Callable]] = {}
        self._deliveries: dict[str, _PyAMQPDelivery] = {}
        self._lock = threading.Lock()
        self._settlement_lock = threading.RLock()
        # Bound cached AMQP sender links. Each unique Event Mesh topic uses a
        # separate AMQP link, so dynamic reply topics can otherwise retain
        # unbounded SendClient instances and sockets for the life of the agent.
        self._send_client_cache_max_size = max(
            int(send_client_cache_max_size or 128), 1
        )
        self._send_client_idle_ttl_seconds = max(
            float(send_client_idle_ttl_seconds or 0.0), 0.0
        )
        self._send_clients: OrderedDict[str, Any] = OrderedDict()
        self._send_client_last_used: dict[str, float] = {}
        self._send_client_locks: dict[str, threading.RLock] = {}
        self._consumer_threads: dict[str, threading.Thread] = {}
        # Per-queue thread-safe queue of (delivery_id, outcome) settlement
        # requests. Producers are dispatch/callback threads; the sole consumer
        # is that queue's own _consume_loop thread, which owns the AMQP link.
        self._settlement_queues: dict[str, "queue.Queue[tuple[str, str]]"] = {}
        self._running = False
        self._dispatch_runtime = ConsumerDispatchRuntime(
            max_in_flight=max_in_flight_messages,
            drain_timeout_seconds=shutdown_grace_seconds,
            logger=logger,
        )
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
                max_in_flight_messages=max_in_flight_messages,
                shutdown_grace_seconds=shutdown_grace_seconds,
            )

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

    def _sleep_before_retry(
        self, attempt_index: int, *, initial: float, maximum: float
    ) -> None:
        delay = self._retry_delay_seconds(
            attempt_index,
            initial=initial,
            maximum=maximum,
        )
        if delay > 0:
            time.sleep(delay)

    @staticmethod
    def _normalize_outcome(value: str, default: str) -> str:
        candidate = (value or "").strip().lower()
        aliases = {
            "ack": "accepted",
            "accept": "accepted",
            "accepted": "accepted",
            "release": "released",
            "released": "released",
            "requeue": "released",
            "reject": "rejected",
            "rejected": "rejected",
        }
        return aliases.get(candidate, default)

    def _import_pyamqp(self) -> tuple[Any, Any, Any, Any, Any]:
        try:
            from agent_sdk.layer4_frameworks.messaging._vendor.azure_pyamqp.authentication import (
                SASLPlainAuth,
            )
            from agent_sdk.layer4_frameworks.messaging._vendor.azure_pyamqp.client import (
                ReceiveClient,
                SendClient,
            )
            from agent_sdk.layer4_frameworks.messaging._vendor.azure_pyamqp.constants import (
                TransportType,
            )
            from agent_sdk.layer4_frameworks.messaging._vendor.azure_pyamqp.message import (
                Message,
                Properties,
            )
        except ImportError as exc:
            raise RuntimeError(
                "EVENT_MESH_PROTOCOL=amqp10ws/amqp10 requires the vendored Azure "
                "pure-Python `_pyamqp` package. Run "
                "python3 patch_agent_sdk_event_mesh_azure_pyamqp.py from the agent-sdk root."
            ) from exc
        return (
            SASLPlainAuth,
            SendClient,
            ReceiveClient,
            TransportType,
            (Message, Properties),
        )

    def _get_access_token(self, *, force_refresh: bool = False) -> str:
        if self._auth_mode not in {"oauth2", "token", "bearer"}:
            return self._client_secret
        now = time.time()
        if (not force_refresh) and self._access_token and now < self._token_expires_at:
            return self._access_token
        if not self._token_url:
            return self._client_secret

        last_exc: Exception | None = None
        for attempt in range(self._token_retry_attempts):
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
                if attempt >= self._token_retry_attempts - 1:
                    break
                if not self._is_retryable_http_exception(exc):
                    break
                logger.debug(
                    "Retrying Event Mesh AMQP OAuth token request attempt=%s",
                    attempt + 1,
                    exc_info=True,
                )
                self._sleep_before_retry(
                    attempt,
                    initial=self._token_retry_initial_delay_seconds,
                    maximum=self._token_retry_max_delay_seconds,
                )

        assert last_exc is not None
        raise last_exc

    def _export_amqp_ws_authorization_for_vendor_transport(
        self,
        *,
        force_refresh: bool = False,
    ) -> None:
        # Publish this instance's OAuth bearer token and endpoint URL to the
        # vendored WebSocket transport for the *current thread only*, via a
        # thread-local context (see _stdlib_websocket.set_amqp_ws_context).
        #
        # This deliberately avoids writing to process-global os.environ: a
        # second client/tenant in the same process would otherwise clobber the
        # first one's credentials and URL globally (tenant-bleed + secret
        # hazard). It also no longer emits any diagnostic describing the token
        # length / JWT structure.
        #
        # The connection is always opened on the same thread that calls this
        # (publisher thread under the per-target send lock, or the consumer
        # thread in _consume_loop), so a thread-local is sufficient and
        # per-instance.
        if self._messaging_protocol != "amqp10ws":
            return

        from agent_sdk.layer4_frameworks.messaging._vendor.azure_pyamqp._stdlib_websocket import (  # noqa: E501
            set_amqp_ws_context,
        )

        # Normalize the token so we never emit "Bearer Bearer <jwt>" or
        # whitespace/newlines inside the HTTP header value.
        authorization = ""
        if self._auth_mode in {"oauth2", "token", "bearer"}:
            raw_token = (
                self._get_access_token(force_refresh=force_refresh) or ""
            ).strip()
            if raw_token:
                if raw_token.lower().startswith("bearer "):
                    parts = raw_token.split(None, 1)
                    bearer_token = parts[1].strip() if len(parts) > 1 else ""
                else:
                    bearer_token = raw_token
                bearer_token = "".join(bearer_token.split())
                if bearer_token:
                    authorization = f"Bearer {bearer_token}"

        set_amqp_ws_context(authorization=authorization, url=self._messaging_url)

    def _endpoint_parts(self) -> tuple[str, int, bool, str]:
        parsed = urlparse(self._messaging_url)
        if not parsed.hostname:
            raise RuntimeError(f"Invalid Event Mesh AMQP URL: {self._messaging_url!r}")
        scheme = parsed.scheme.lower()
        websocket = scheme in {"ws", "wss", "https"} or "ws" in self._messaging_protocol
        default_port = 443 if scheme in {"wss", "amqps", "https"} or websocket else 5672
        path = parsed.path or ""
        if websocket and not path:
            path = "/protocols/amqp10ws"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return parsed.hostname, parsed.port or default_port, websocket, path

    def _hostname(self) -> str:
        """Return the host/path shape expected by Azure _pyamqp.

        Azure _pyamqp's WebSocketTransport builds the final WebSocket URL
        internally. For amqp10ws, pass host/path, not wss://host/path.
        SAP Event Mesh requires /protocols/amqp10ws on the WebSocket upgrade
        request, so preserve that path.
        """
        raw = (self._messaging_url or "").strip()
        if not raw:
            return raw

        value = raw
        for _ in range(3):
            lowered = value.lower()
            stripped = False
            for scheme in (
                "wss://",
                "ws://",
                "https://",
                "http://",
                "amqps://",
                "amqp://",
            ):
                if lowered.startswith(scheme):
                    value = value[len(scheme) :]
                    stripped = True
                    break
            if not stripped:
                break

        authority, sep, path_tail = value.partition("/")
        path = f"/{path_tail}" if sep else ""

        host = authority
        port = ""

        if host.startswith("[") and "]" in host:
            bracket_end = host.find("]")
            remainder = host[bracket_end + 1 :]
            if remainder.startswith(":") and remainder[1:].isdigit():
                port = remainder[1:]
            host = host[: bracket_end + 1]
        elif ":" in host:
            maybe_host, maybe_port = host.rsplit(":", 1)
            if maybe_port.isdigit() and 0 < int(maybe_port) <= 65535:
                host = maybe_host
                port = maybe_port

        is_websocket = self._messaging_protocol == "amqp10ws"
        default_port = "443" if is_websocket else "5671"

        if is_websocket and not path:
            path = "/protocols/amqp10ws"

        authority_out = host
        if port and port != default_port:
            authority_out = f"{host}:{port}"

        return f"{authority_out}{path}" if is_websocket else authority_out

    def _custom_endpoint(self) -> str | None:
        hostname, port, websocket, path = self._endpoint_parts()
        if not websocket:
            return None
        return f"{hostname}:{port}{path or '/protocols/amqp10ws'}"

    def _ssl_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        if not self._verify_ssl:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    def _client_kwargs(self) -> dict[str, Any]:
        self._export_amqp_ws_authorization_for_vendor_transport()
        SASLPlainAuth, _, _, TransportType, _ = self._import_pyamqp()
        _, _, websocket, _ = self._endpoint_parts()
        transport_type = (
            TransportType.AmqpOverWebsocket if websocket else TransportType.Amqp
        )

        kwargs: dict[str, Any] = {
            "transport_type": transport_type,
            "network_trace": self._debug,
            "socket_timeout": self._poll_timeout_seconds,
            "ssl_context": self._ssl_context(),
            "keep_alive_interval": 30,
        }

        # SAP Event Mesh amqp10ws + OAuth/Bearer authenticates during the
        # WebSocket HTTP Upgrade via:
        #   Authorization: Bearer <token>
        #
        # The AMQP SASL layer still needs an auth object because this vendored
        # client calls `self._auth.sasl` unconditionally in AMQPClient.open().
        # Do not omit auth; provide SASL ANONYMOUS explicitly for this path.
        use_ws_bearer_auth = websocket and self._auth_mode in {
            "oauth2",
            "token",
            "bearer",
        }
        if use_ws_bearer_auth:
            from agent_sdk.layer4_frameworks.messaging._vendor.azure_pyamqp.sasl import (
                SASLAnonymousCredential,
            )

            class _SASLAnonymousAuth:
                # Any value different from AUTH_TYPE_CBS is enough; AMQPClient
                # only checks equality with AUTH_TYPE_CBS after opening.
                auth_type = "sasl_anonymous"

                def __init__(self) -> None:
                    self.sasl = SASLAnonymousCredential()

            kwargs["auth"] = _SASLAnonymousAuth()
        else:
            password = (
                self._client_secret
                if self._auth_mode in {"clientsecret", "client-secret", "basic"}
                else self._get_access_token()
            )
            kwargs["auth"] = SASLPlainAuth(self._client_id, password)

        custom_endpoint = self._custom_endpoint()
        if custom_endpoint:
            kwargs["custom_endpoint_address"] = custom_endpoint
        return kwargs

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
        return self._format_address(self._topic_address_template, topic=topic)

    def _queue_address(self, queue_name: str) -> str:
        return self._format_address(self._queue_address_template, queue=queue_name)

    def _send_lock_for_target(self, target: str) -> threading.RLock:
        with self._lock:
            lock = self._send_client_locks.get(target)
            if lock is None:
                lock = threading.RLock()
                self._send_client_locks[target] = lock
            return lock

    def _create_send_client(self, target: str) -> Any:
        _, SendClient, _, _, _ = self._import_pyamqp()
        self._export_amqp_ws_authorization_for_vendor_transport()
        return SendClient(self._hostname(), target, **self._client_kwargs())

    @staticmethod
    def _close_send_client_safely(client: Any) -> None:
        try:
            client.close()
        except Exception:
            logger.debug(
                "Failed to close cached AMQP send client cleanly",
                exc_info=True,
            )

    def _evict_send_client_locked(self, target: str) -> Any | None:
        """Evict a cached sender while ``self._lock`` is held.

        The per-target send lock is acquired non-blocking before eviction so we
        do not close a sender that another publisher thread is actively using.
        """
        lock = self._send_client_locks.get(target)
        lock_acquired = False
        if lock is not None:
            lock_acquired = lock.acquire(blocking=False)
            if not lock_acquired:
                return None

        try:
            client = self._send_clients.pop(target, None)
            self._send_client_last_used.pop(target, None)
            if client is not None:
                self._send_client_locks.pop(target, None)
            return client
        finally:
            if lock_acquired and lock is not None:
                lock.release()

    def _prune_send_client_cache_locked(
        self,
        now: float,
        *,
        exclude_target: str = "",
    ) -> list[Any]:
        """Return stale/overflow cached senders to close outside the main lock."""
        clients_to_close: list[Any] = []

        if self._send_client_idle_ttl_seconds > 0:
            expired_targets = [
                target
                for target, last_used in list(self._send_client_last_used.items())
                if target != exclude_target
                and now - last_used >= self._send_client_idle_ttl_seconds
            ]
            for target in expired_targets:
                client = self._evict_send_client_locked(target)
                if client is not None:
                    clients_to_close.append(client)

        while len(self._send_clients) > self._send_client_cache_max_size:
            evicted = False
            for target in list(self._send_clients.keys()):
                if target == exclude_target:
                    continue
                client = self._evict_send_client_locked(target)
                if client is not None:
                    clients_to_close.append(client)
                    evicted = True
                    break
            if not evicted:
                break

        return clients_to_close

    def _get_send_client(self, target: str) -> Any:
        clients_to_close: list[Any] = []
        now = time.monotonic()

        with self._lock:
            client = self._send_clients.get(target)
            if client is not None:
                self._send_clients.move_to_end(target)
                self._send_client_last_used[target] = now
                clients_to_close = self._prune_send_client_cache_locked(
                    now,
                    exclude_target=target,
                )
            else:
                client = self._create_send_client(target)
                self._send_clients[target] = client
                self._send_client_last_used[target] = now
                clients_to_close = self._prune_send_client_cache_locked(
                    now,
                    exclude_target=target,
                )

        for stale_client in clients_to_close:
            self._close_send_client_safely(stale_client)
        return client

    def _discard_send_client(self, target: str, client: Any | None = None) -> None:
        with self._lock:
            current = self._send_clients.get(target)
            if client is not None and current is not client:
                return
            client_to_close = self._send_clients.pop(target, None)
            self._send_client_last_used.pop(target, None)
            # Keep the per-target lock here. publish_to_topic() calls this while
            # holding that lock and then retries on the same lock object; removing
            # it would allow a concurrent publisher to create a second lock for
            # the same target and race the retry.

        if client_to_close is not None:
            self._close_send_client_safely(client_to_close)

    def _close_send_clients(self) -> None:
        with self._lock:
            clients = list(self._send_clients.values())
            self._send_clients.clear()
            self._send_client_last_used.clear()
            self._send_client_locks.clear()

        for client in clients:
            self._close_send_client_safely(client)

    def publish_to_topic(
        self, topic: str, message: Any, key: str | None = None
    ) -> None:
        _, _, _, _, message_types = self._import_pyamqp()
        Message, Properties = message_types
        target = self._topic_address(topic)
        payload = (
            json.dumps(message).encode("utf-8")
            if isinstance(message, (dict, list))
            else str(message).encode("utf-8")
        )
        properties = Properties(correlation_id=key) if key else None
        amqp_message = Message(data=[payload], properties=properties)

        send_lock = self._send_lock_for_target(target)
        last_exc: Exception | None = None
        with send_lock:
            for attempt in range(self._publish_retry_attempts):
                client = self._get_send_client(target)
                try:
                    client.send_message(
                        amqp_message,
                        timeout=self._request_timeout_seconds,
                    )
                    return
                except Exception as exc:
                    last_exc = exc
                    logger.debug(
                        "AMQP publish failed; reconnecting target=%s attempt=%s/%s",
                        target,
                        attempt + 1,
                        self._publish_retry_attempts,
                        exc_info=True,
                    )
                    self._discard_send_client(target, client)

                    if attempt >= self._publish_retry_attempts - 1:
                        break

                    if self._auth_mode in {"oauth2", "token", "bearer"}:
                        try:
                            self._get_access_token(force_refresh=True)
                            self._export_amqp_ws_authorization_for_vendor_transport(
                                force_refresh=False
                            )
                        except Exception:
                            logger.debug(
                                "Failed to refresh AMQP OAuth token before publish retry",
                                exc_info=True,
                            )

                    self._sleep_before_retry(
                        attempt,
                        initial=self._publish_retry_initial_delay_seconds,
                        maximum=self._publish_retry_max_delay_seconds,
                    )

        assert last_exc is not None
        raise last_exc

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
        try:
            self.ensure_queue(queue_name)
            self.ensure_queue_subscription(queue_name, topic_pattern)
        except Exception:
            logger.exception(
                "Failed to auto-provision Event Mesh queue/subscription (queue=%s, topic=%s)",
                queue_name,
                topic_pattern,
            )
        with self._lock:
            if queue_name not in self._consumer_threads:
                self._running = True
                thread = threading.Thread(
                    target=self._consume_loop,
                    args=(queue_name,),
                    name=f"event-mesh-amqp-consumer-{queue_name}",
                    daemon=True,
                )
                self._consumer_threads[queue_name] = thread
                thread.start()

    def _register_delivery(self, delivery: _PyAMQPDelivery) -> str:
        delivery_id = uuid.uuid4().hex
        with self._lock:
            self._deliveries[delivery_id] = delivery
        return delivery_id

    def _request_settlement(self, delivery_id: str, outcome: str) -> None:
        """Marshal a settlement request onto the owning consumer thread.

        The AMQP disposition frame written by ``settle_messages`` shares the
        (non-thread-safe) connection/session with the consumer's receive loop.
        ack/nack/reject are typically invoked from the dispatch runtime's
        asyncio thread, so we never settle inline here; instead we enqueue the
        request and let ``_consume_loop`` perform it between receive iterations
        on the thread that owns the link. This call never blocks on the consumer
        thread, so it cannot deadlock.
        """
        settle_q: "queue.Queue[tuple[str, str]] | None" = None
        with self._lock:
            delivery = self._deliveries.get(delivery_id)
            if delivery is None or delivery.settle_requested:
                return
            delivery.settle_requested = True
            settle_q = self._settlement_queues.get(delivery.queue_name)
            if settle_q is None:
                # No live consumer owns this delivery (loop stopped/reconnected);
                # settling on another thread is unsafe, so just drop the tracking
                # entry. The broker will redeliver the unsettled message.
                self._deliveries.pop(delivery_id, None)
        if settle_q is not None:
            settle_q.put((delivery_id, outcome))

    def _drain_settlement_queue(self, settle_q: "queue.Queue[tuple[str, str]]") -> None:
        """Perform queued settlements on the current (consumer) thread."""
        while True:
            try:
                delivery_id, outcome = settle_q.get_nowait()
            except queue.Empty:
                return
            self._perform_settlement(delivery_id, outcome)

    def _perform_settlement(self, delivery_id: str, outcome: str) -> None:
        # MUST run on the consumer thread that owns the AMQP link.
        with self._lock:
            delivery = self._deliveries.get(delivery_id)
        if delivery is None:
            return
        try:
            delivery.settle(outcome)
        except Exception:
            logger.warning(
                "Failed to settle AMQP message (delivery_id=%s, outcome=%s)",
                delivery_id,
                outcome,
                exc_info=True,
            )
        with self._lock:
            self._deliveries.pop(delivery_id, None)

    def _on_callback_complete(self, delivery_id: str, *, errored: bool) -> None:
        """Auto-settle a delivery once all of its callbacks have finished.

        Guarantees ``_deliveries`` stays bounded: a callback that neither acks
        nor nacks (by omission or after raising) still results in the delivery
        being settled and evicted, and the message being settled on the broker.
        """
        outcome: str | None = None
        settle_q: "queue.Queue[tuple[str, str]] | None" = None
        with self._lock:
            delivery = self._deliveries.get(delivery_id)
            if delivery is None:
                return
            if errored:
                delivery.errored = True
            if delivery.pending > 0:
                delivery.pending -= 1
            if delivery.pending > 0:
                return
            if delivery.settle_requested:
                return
            delivery.settle_requested = True
            outcome = (
                self._default_error_settlement_outcome
                if delivery.errored
                else self._default_settlement_outcome
            )
            settle_q = self._settlement_queues.get(delivery.queue_name)
        if settle_q is not None and outcome is not None:
            settle_q.put((delivery_id, outcome))

    def ack_message(self, queue_name: str, message_headers: dict[str, str]) -> None:
        delivery_id = message_headers.get("x-agent-sdk-amqp-delivery-id", "")
        if delivery_id:
            self._request_settlement(delivery_id, "accepted")

    def nack_message(
        self, queue_name: str, message_headers: dict[str, str], requeue: bool = True
    ) -> None:
        delivery_id = message_headers.get("x-agent-sdk-amqp-delivery-id", "")
        if delivery_id:
            self._request_settlement(delivery_id, "released" if requeue else "rejected")

    def reject_message(
        self, queue_name: str, message_headers: dict[str, str], requeue: bool = False
    ) -> None:
        self.nack_message(queue_name, message_headers, requeue=requeue)

    async def _invoke_callback(
        self,
        callback: Callable,
        payload: Any,
        headers: dict[str, str],
        queue_name: str,
    ) -> bool:
        """Invoke a user callback with isolation.

        Returns True on success, False if the callback raised. Exceptions are
        logged and swallowed (like the REST base class ``_run_handler``) so an
        uncaught callback error never escapes into the dispatch runtime.
        """
        try:
            try:
                inspect.signature(callback).bind(payload, headers, queue_name)
            except (TypeError, ValueError):
                result = callback(payload, headers)
            else:
                result = callback(payload, headers, queue_name)
            if inspect.isawaitable(result):
                await result
            return True
        except Exception:
            logger.exception("Error in AMQP callback for queue=%s", queue_name)
            return False

    async def _dispatch_callback(
        self,
        callback: Callable,
        payload: Any,
        headers: dict[str, str],
        queue_name: str,
        delivery_id: str,
    ) -> None:
        errored = False
        try:
            errored = not await self._invoke_callback(
                callback, payload, headers, queue_name
            )
        finally:
            self._on_callback_complete(delivery_id, errored=errored)

    @staticmethod
    def _decode_body(message: Any) -> Any:
        body = getattr(message, "data", None)
        if body is None:
            body = getattr(message, "value", None)
        if isinstance(body, (bytes, bytearray)):
            raw = bytes(body)
        elif isinstance(body, str):
            raw = body.encode("utf-8")
        else:
            parts: list[bytes] = []
            try:
                for chunk in body or []:
                    if isinstance(chunk, (bytes, bytearray)):
                        parts.append(bytes(chunk))
                    else:
                        parts.append(str(chunk).encode("utf-8"))
                raw = b"".join(parts)
            except TypeError:
                raw = str(body).encode("utf-8")
        text = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text

    @staticmethod
    def _split_received_item(item: Any) -> tuple[Any | None, Any]:
        if isinstance(item, tuple) and len(item) == 2:
            return item[0], item[1]
        return None, item

    def _create_receive_client(
        self, source: str, *, force_token_refresh: bool = False
    ) -> Any:
        _, _, ReceiveClient, _, _ = self._import_pyamqp()
        if force_token_refresh and self._auth_mode in {"oauth2", "token", "bearer"}:
            try:
                self._get_access_token(force_refresh=True)
            except Exception:
                logger.debug(
                    "Failed to refresh AMQP OAuth token before receive reconnect",
                    exc_info=True,
                )
        # Primes the per-thread transport auth/URL context on this (consumer)
        # thread so a reconnect always uses a fresh token instead of a stale one.
        self._export_amqp_ws_authorization_for_vendor_transport()
        return ReceiveClient(
            self._hostname(),
            source,
            link_credit=self._prefetch,
            **self._client_kwargs(),
        )

    @staticmethod
    def _close_receive_client_safely(client: Any) -> None:
        if client is None:
            return
        try:
            client.close()
        except Exception:
            logger.debug("Failed to close AMQP receive client cleanly", exc_info=True)

    def _dispatch_delivery(
        self,
        delivery: _PyAMQPDelivery,
        delivery_id: str,
        callbacks: list[Callable],
        payload: Any,
        headers: dict[str, str],
        queue_name: str,
    ) -> None:
        if not callbacks:
            # No handler will run; auto-settle immediately so neither the
            # in-memory delivery nor the broker message is left dangling.
            self._on_callback_complete(delivery_id, errored=False)
            return
        with self._lock:
            delivery.pending = len(callbacks)
        control = is_agent_control_message(payload)
        conv_id = "" if control else extract_conv_id(payload)
        for callback in callbacks:
            try:
                dispatch_coro = self._dispatch_callback(
                    callback, payload, headers, queue_name, delivery_id
                )
                # SA-892 (N4): control messages (stop) bypass the in-flight
                # semaphore via submit_control so a stop is never queued behind
                # the running turn under max_in_flight=1.
                if control:
                    self._dispatch_runtime.submit_control(dispatch_coro)
                else:
                    self._dispatch_runtime.submit(dispatch_coro, conv_id=conv_id)
            except RuntimeError:
                if not self._running:
                    return
                logger.exception(
                    "Dispatch runtime rejected AMQP callback for queue=%s",
                    queue_name,
                )
                # The coroutine was closed and will never run; account for it so
                # the delivery can still be settled and evicted.
                self._on_callback_complete(delivery_id, errored=True)

    def _consume_loop(self, queue_name: str) -> None:
        source = self._queue_address(queue_name)
        settle_q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        with self._lock:
            self._settlement_queues[queue_name] = settle_q
        client = self._create_receive_client(source)
        try:
            while self._running:
                try:
                    # Settle any acks/nacks marshalled from dispatch threads on
                    # this (link-owning) thread before/after receiving.
                    self._drain_settlement_queue(settle_q)
                    received_any = False
                    for item in client.receive_messages_iter(
                        timeout=self._poll_timeout_seconds
                    ):
                        if not self._running:
                            return
                        received_any = True
                        frame, message = self._split_received_item(item)
                        payload = self._decode_body(message)
                        delivery = _PyAMQPDelivery(
                            client=client,
                            frame=frame,
                            message=message,
                            settlement_lock=self._settlement_lock,
                            queue_name=queue_name,
                        )
                        delivery_id = self._register_delivery(delivery)
                        headers = {"x-agent-sdk-amqp-delivery-id": delivery_id}
                        with self._lock:
                            callbacks = list(self._callbacks.get(queue_name, []))
                        self._dispatch_delivery(
                            delivery,
                            delivery_id,
                            callbacks,
                            payload,
                            headers,
                            queue_name,
                        )
                        self._drain_settlement_queue(settle_q)
                    if not received_any:
                        self._drain_settlement_queue(settle_q)
                        time.sleep(self._poll_timeout_seconds)
                except (TimeoutError, socket.timeout):
                    # Benign receive timeout; keep polling the same connection.
                    continue
                except Exception:
                    if self._running:
                        logger.exception(
                            "AMQP receive failed for queue=%s; reconnecting",
                            queue_name,
                        )
                    # A non-timeout error leaves the ReceiveClient half-open;
                    # reusing it makes open() a no-op (session still set) and
                    # spins a tight error loop. Fully recreate it with a fresh
                    # token instead.
                    self._close_receive_client_safely(client)
                    if not self._running:
                        break
                    time.sleep(self._poll_timeout_seconds)
                    if not self._running:
                        break
                    try:
                        client = self._create_receive_client(
                            source, force_token_refresh=True
                        )
                    except Exception:
                        logger.exception(
                            "Failed to recreate AMQP receive client for queue=%s",
                            queue_name,
                        )
                        time.sleep(self._poll_timeout_seconds)
        finally:
            with self._lock:
                self._settlement_queues.pop(queue_name, None)
            # Flush settlements that arrived during shutdown on this thread.
            self._drain_settlement_queue(settle_q)
            self._close_receive_client_safely(client)

    def cancel_conversation(self, conv_id: str) -> int:
        """SA-892: cooperatively cancel a conversation's in-flight turn(s)."""
        return self._dispatch_runtime.cancel(conv_id)

    def close(self) -> None:
        self._running = False
        self._close_send_clients()
        self._dispatch_runtime.close_for_new_work()
        for thread in list(self._consumer_threads.values()):
            thread.join(timeout=10)
        self._consumer_threads.clear()
        self._dispatch_runtime.drain_and_close()
        if self._provisioner is not None:
            self._provisioner.close()
        self._http.close()

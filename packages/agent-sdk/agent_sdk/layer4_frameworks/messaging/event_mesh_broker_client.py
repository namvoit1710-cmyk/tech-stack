from __future__ import annotations

import inspect
import json
import logging
import threading
import time
from typing import Any, Callable
from urllib.parse import quote

import httpx

from agent_sdk.layer1_domain.value_objects.agent_control import (
    extract_conv_id,
    is_agent_control_message,
)
from agent_sdk.layer4_frameworks.messaging.consumer_dispatch_runtime import (
    ConsumerDispatchRuntime,
)

logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)


class EventMeshBrokerClient:
    def __init__(
        self,
        token_url: str,
        messaging_url: str,
        management_url: str,
        client_id: str,
        client_secret: str,
        namespace: str = "default",
        verify_ssl: bool = True,
        poll_interval_seconds: float = 1.0,
        request_timeout_seconds: float = 30.0,
        max_in_flight_messages: int = 4,
        shutdown_grace_seconds: int = 30,
    ) -> None:
        self._token_url = token_url
        self._messaging_base_url = self._normalize_messaging_base_url(messaging_url)
        self._management_base_url = self._normalize_management_base_url(management_url)
        self._client_id = client_id
        self._client_secret = client_secret
        self._namespace = namespace
        self._poll_interval_seconds = poll_interval_seconds

        self._http = httpx.Client(verify=verify_ssl, timeout=request_timeout_seconds)
        self._access_token = ""
        self._token_expires_at = 0.0

        self._callbacks: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()
        self._consumer_thread: threading.Thread | None = None
        self._running = False
        self._dispatch_runtime = ConsumerDispatchRuntime(
            max_in_flight=max_in_flight_messages,
            drain_timeout_seconds=shutdown_grace_seconds,
            logger=logger,
        )

    @staticmethod
    def _normalize_messaging_base_url(raw_url: str) -> str:
        value = raw_url.rstrip("/")
        if value.endswith("/messagingrest/v1"):
            return value
        return f"{value}/messagingrest/v1"

    @staticmethod
    def _normalize_management_base_url(raw_url: str) -> str:
        value = raw_url.rstrip("/")
        if value.endswith("/hub/rest/api/v1/management/messaging"):
            return value
        return f"{value}/hub/rest/api/v1/management/messaging"

    def _get_access_token(self, *, force_refresh: bool = False) -> str:
        now = time.time()
        if (not force_refresh) and self._access_token and now < self._token_expires_at:
            return self._access_token

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
        self._token_expires_at = now + max(expires_in - 30, 30)
        return token

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        expected: set[int] | None = None,
        allow_404: bool = False,
    ) -> httpx.Response:
        token = self._get_access_token()
        merged_headers = {"Authorization": f"Bearer {token}"}
        if headers:
            merged_headers.update(headers)

        response = self._http.request(
            method, url, headers=merged_headers, json=json_body
        )
        if response.status_code == 401:
            token = self._get_access_token(force_refresh=True)
            merged_headers["Authorization"] = f"Bearer {token}"
            response = self._http.request(
                method, url, headers=merged_headers, json=json_body
            )

        if allow_404 and response.status_code == 404:
            return response

        if expected is not None and response.status_code not in expected:
            response.raise_for_status()
        elif expected is None:
            response.raise_for_status()
        return response

    def _build_topic_path(self, subject: str) -> str:
        return f"{self._namespace}/{subject}" if self._namespace else subject

    def _build_queue_name(self, subject: str) -> str:
        return f"{self._namespace}/{subject}" if self._namespace else subject

    def build_queue_name(self, subject: str) -> str:
        return self._build_queue_name(subject)

    def publish_to_topic(
        self, topic: str, message: Any, key: str | None = None
    ) -> None:
        topic_path = self._build_topic_path(topic)
        encoded_topic = quote(topic_path, safe="")
        url = f"{self._messaging_base_url}/topics/{encoded_topic}/messages"
        headers = {
            "Content-Type": "application/json",
            "x-qos": "0",
        }
        if key:
            headers["x-correlationid"] = key

        self._request(
            "POST",
            url,
            headers=headers,
            json_body=message,
            expected={200, 201, 202, 204},
        )

    def ensure_queue(self, queue_name: str) -> None:
        encoded_queue = quote(queue_name, safe="")
        url = f"{self._management_base_url}/queues/{encoded_queue}"
        self._request(
            "PUT",
            url,
            headers={"Content-Type": "application/json"},
            json_body={"maxMessageSizeInBytes": 10000000},
            expected={200, 201, 204, 409},
            allow_404=True,
        )

    def ensure_queue_subscription(self, queue_name: str, topic_pattern: str) -> None:
        encoded_queue = quote(queue_name, safe="")
        encoded_pattern = quote(topic_pattern, safe="")
        url = (
            f"{self._management_base_url}/queues/"
            f"{encoded_queue}/subscriptions/{encoded_pattern}"
        )
        self._request(
            "PUT",
            url,
            headers={"Content-Type": "application/json"},
            json_body={},
            expected={200, 201, 204, 409},
            allow_404=True,
        )

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
        self,
        queue_name: str,
        topic: str,
        callback: Callable,
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
            if self._consumer_thread is None:
                self._running = True
                self._consumer_thread = threading.Thread(
                    target=self._consume_loop,
                    name="event-mesh-consumer-loop",
                    daemon=True,
                )
                self._consumer_thread.start()

    def _consume_message(self, queue_name: str) -> tuple[Any | None, dict[str, str]]:
        encoded_queue = quote(queue_name, safe="")
        url = f"{self._messaging_base_url}/queues/{encoded_queue}/messages/consumption"

        response = self._request(
            "POST",
            url,
            headers={"Content-Type": "application/json", "x-qos": "0"},
            json_body={"maxMessages": 1},
            expected={200, 204},
        )
        if response.status_code == 204:
            return None, {}

        headers = {k.lower(): v for k, v in response.headers.items()}
        try:
            message_data = response.json()
        except ValueError:
            text = response.text or ""
            if not text:
                return None, headers
            message_data = {"data": text}

        message_content = message_data
        if isinstance(message_content, str):
            try:
                message_content = json.loads(message_content)
            except (json.JSONDecodeError, ValueError):
                pass

        return message_content, headers

    def _ack_message(self, queue_name: str, message_headers: dict[str, str]) -> None:
        message_id = message_headers.get("x-message-id")
        if not message_id:
            return

        encoded_queue = quote(queue_name, safe="")
        encoded_message_id = quote(message_id, safe="")
        url = f"{self._messaging_base_url}/queues/{encoded_queue}/messages/{encoded_message_id}/acknowledgement"

        try:
            self._request("POST", url, expected={200, 202, 204, 404})
        except Exception as e:
            logger.warning("Failed to acknowledge message %s: %s", message_id, e)

    def ack_message(self, queue_name: str, message_headers: dict[str, str]) -> None:
        self._ack_message(queue_name, message_headers)

    def nack_message(
        self,
        queue_name: str,
        message_headers: dict[str, str],
        requeue: bool = True,
    ) -> None:
        self.reject_message(queue_name, message_headers, requeue=requeue)

    def reject_message(
        self,
        queue_name: str,
        message_headers: dict[str, str],
        requeue: bool = False,
    ) -> None:
        if requeue:
            return
        self._ack_message(queue_name, message_headers)

    async def _run_handler(
        self,
        handler: Callable,
        payload: Any,
        message_headers: dict[str, str],
        queue_name: str,
    ) -> None:
        try:
            try:
                inspect.signature(handler).bind(payload, message_headers, queue_name)
            except (TypeError, ValueError):
                args = (payload, message_headers)
            else:
                args = (payload, message_headers, queue_name)

            result = handler(*args)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("Error in callback for queue=%s", queue_name)

    def _consume_loop(self) -> None:
        backoff = self._poll_interval_seconds
        max_backoff = 60.0
        try:
            while self._running:
                with self._lock:
                    subscriptions = list(self._callbacks.items())

                had_error = False
                message_consumed = False
                for queue_name, handlers in subscriptions:
                    try:
                        payload, message_headers = self._consume_message(queue_name)
                        if payload is None:
                            continue
                    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                        logger.warning(
                            "Event Mesh connection error for queue=%s: %s (retrying in %.1fs)",
                            queue_name,
                            exc,
                            backoff,
                        )
                        had_error = True
                        continue
                    except Exception:
                        logger.exception(
                            "Event Mesh consume error for queue=%s", queue_name
                        )
                        had_error = True
                        continue

                    message_consumed = True
                    control = is_agent_control_message(payload)
                    conv_id = "" if control else extract_conv_id(payload)
                    for handler in handlers:
                        try:
                            run_coro = self._run_handler(
                                handler,
                                payload,
                                message_headers,
                                queue_name,
                            )
                            # SA-892 (N4): control (stop) bypasses the in-flight
                            # semaphore so it is not starved behind a running
                            # turn under max_in_flight=1.
                            if control:
                                self._dispatch_runtime.submit_control(run_coro)
                            else:
                                self._dispatch_runtime.submit(
                                    run_coro, conv_id=conv_id
                                )
                        except RuntimeError:
                            if not self._running:
                                return
                            logger.exception(
                                "Dispatch runtime rejected callback for queue=%s",
                                queue_name,
                            )

                if had_error:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)
                elif message_consumed:
                    backoff = self._poll_interval_seconds
                    continue  # immediately poll again without sleeping
                else:
                    backoff = self._poll_interval_seconds
                    time.sleep(self._poll_interval_seconds)
        finally:
            self._running = False

    def cancel_conversation(self, conv_id: str) -> int:
        """SA-892: cooperatively cancel a conversation's in-flight turn(s)."""
        return self._dispatch_runtime.cancel(conv_id)

    def close(self) -> None:
        self._running = False
        self._dispatch_runtime.close_for_new_work()
        if self._consumer_thread is not None:
            self._consumer_thread.join(timeout=10)
            self._consumer_thread = None
        self._dispatch_runtime.drain_and_close()
        self._http.close()

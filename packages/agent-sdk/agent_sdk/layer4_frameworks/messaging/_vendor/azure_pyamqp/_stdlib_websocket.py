from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_DEFAULT_SUBPROTOCOL = "AMQPWSB10"
_DEFAULT_MAX_FRAME_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_MESSAGE_BYTES = 64 * 1024 * 1024

# Per-thread SAP Event Mesh WebSocket auth/URL override. The AMQP broker client
# sets this on the thread that opens a connection so that multiple clients or
# tenants running in the same process do not clobber each other's bearer token
# or endpoint URL via process-global os.environ. Falls back to os.environ when
# unset to preserve backward compatibility.
_ws_context = threading.local()


def set_amqp_ws_context(*, authorization: str = "", url: str = "") -> None:
    """Set per-thread SAP Event Mesh WebSocket authorization header and URL."""
    _ws_context.authorization = authorization or ""
    _ws_context.url = url or ""


def clear_amqp_ws_context() -> None:
    """Clear the per-thread SAP Event Mesh WebSocket context."""
    _ws_context.authorization = ""
    _ws_context.url = ""


def _context_authorization() -> str:
    return getattr(_ws_context, "authorization", "") or ""


def _context_url() -> str:
    return getattr(_ws_context, "url", "") or ""


def _websocket_max_bytes(env_name: str, default: int) -> int:
    configured = os.environ.get(env_name, "").strip()
    if configured:
        try:
            value = int(configured)
            if value > 0:
                return value
        except ValueError:
            pass
    return default


class WebSocketProtocolError(ConnectionError):
    """Compatibility error expected by Azure _pyamqp's transport layer."""


class WebSocketTimeoutError(TimeoutError):
    """Compatibility timeout expected by Azure _pyamqp's transport layer."""


class WebSocketConnectionClosedException(ConnectionError):
    """Compatibility closed-connection error expected by WebSocket callers."""


@dataclass(frozen=True)
class _ParsedWebSocketUrl:
    scheme: str
    host: str
    port: int
    path: str


def _event_mesh_amqp_ws_fallback_url() -> str:
    """Return configured SAP Event Mesh AMQP-over-WebSocket URL, if present."""
    for name in (
        "EVENT_MESH_MESSAGING_AMQP10WS_URL",
        "EVENT_MESH_AMQP10WS_URL",
        "EVENT_MESH_MESSAGING_URL",
        "EVENT_MESH_BROKER_URL",
    ):
        value = os.environ.get(name, "").strip()
        if not value:
            continue
        lowered = value.lower()
        if "amqp10ws" in lowered or lowered.startswith(("wss://", "ws://")):
            return value
    return ""


def _looks_like_lost_websocket_host(raw_url: str) -> bool:
    raw = raw_url.strip()
    lowered = raw.lower()
    if not raw:
        return True
    if "://none" in lowered or lowered.startswith("none"):
        return True
    if lowered in {"wss://", "ws://", "https://", "http://"}:
        return True
    # Previous broken transport composition produced exactly this family:
    # wss://None:443443/protocols/amqp10ws
    if "none:443" in lowered:
        return True
    return False


def _repair_nested_url(raw_url: str) -> str:
    """Repair nested/malformed WebSocket URL values.

    Also recovers from Azure _pyamqp losing the SAP Event Mesh host and handing
    us values such as ``wss://None:443443/protocols/amqp10ws`` by falling back
    to EVENT_MESH_MESSAGING_AMQP10WS_URL.
    """
    raw = raw_url.strip()

    if _looks_like_lost_websocket_host(raw):
        fallback = _event_mesh_amqp_ws_fallback_url()
        if fallback:
            raw = fallback

    for _ in range(5):
        lowered = raw.lower()
        changed = False
        for outer in ("wss://", "ws://", "https://", "http://"):
            if not lowered.startswith(outer):
                continue
            rest = raw[len(outer) :]
            rest_lower = rest.lower()
            for inner in ("wss://", "ws://", "https://", "http://"):
                if rest_lower.startswith(inner):
                    raw = rest
                    changed = True
                    break
            if changed:
                break
        if not changed:
            break

    if _looks_like_lost_websocket_host(raw):
        fallback = _event_mesh_amqp_ws_fallback_url()
        if fallback:
            raw = fallback

    return raw


def _manual_parse_url(raw_url: str) -> tuple[str, str, int | None, str]:
    raw = raw_url.strip()
    scheme = "wss"
    rest = raw
    if "://" in rest:
        scheme, rest = rest.split("://", 1)
        scheme = scheme.lower() or "wss"

    authority, sep, path_part = rest.partition("/")
    path = f"/{path_part}" if sep else "/"
    if not authority:
        raise ValueError(f"Invalid WebSocket URL without host: {raw_url!r}")

    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]

    if authority.startswith("["):
        end = authority.find("]")
        if end == -1:
            raise ValueError(f"Invalid IPv6 WebSocket authority: {authority!r}")
        host = authority[1:end]
        remainder = authority[end + 1 :]
        port = None
        if remainder.startswith(":") and remainder[1:].isdigit():
            candidate = int(remainder[1:])
            if 0 < candidate <= 65535:
                port = candidate
        return scheme, host, port, path

    parts = authority.split(":")
    host = parts[0].strip()
    port: int | None = None

    for part in reversed(parts[1:]):
        if part.isdigit():
            candidate = int(part)
            if 0 < candidate <= 65535:
                port = candidate
                break

    if not host:
        raise ValueError(f"Invalid WebSocket authority without host: {authority!r}")

    return scheme, host, port, path


def _parse_url(raw_url: str) -> _ParsedWebSocketUrl:
    raw = _repair_nested_url(raw_url)
    if not raw:
        raise ValueError("WebSocket URL must not be empty")
    if "://" not in raw:
        raw = f"wss://{raw}"

    parsed = urlsplit(raw)
    scheme = (parsed.scheme or "wss").lower()
    if scheme == "https":
        scheme = "wss"
    elif scheme == "http":
        scheme = "ws"

    try:
        parsed_port = parsed.port
    except ValueError:
        manual_scheme, manual_host, manual_port, manual_path = _manual_parse_url(raw)
        if manual_scheme == "https":
            manual_scheme = "wss"
        elif manual_scheme == "http":
            manual_scheme = "ws"
        default_port = 443 if manual_scheme == "wss" else 80
        return _ParsedWebSocketUrl(
            scheme=manual_scheme,
            host=manual_host,
            port=manual_port or default_port,
            path=manual_path or "/",
        )

    host = parsed.hostname
    if not host:
        manual_scheme, manual_host, manual_port, manual_path = _manual_parse_url(raw)
        if manual_scheme == "https":
            manual_scheme = "wss"
        elif manual_scheme == "http":
            manual_scheme = "ws"
        default_port = 443 if manual_scheme == "wss" else 80
        return _ParsedWebSocketUrl(
            scheme=manual_scheme,
            host=manual_host,
            port=manual_port or default_port,
            path=manual_path or "/",
        )

    default_port = 443 if scheme == "wss" else 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    return _ParsedWebSocketUrl(
        scheme=scheme,
        host=host,
        port=parsed_port or default_port,
        path=path,
    )


def _normalize_extra_headers(header: Any) -> list[str]:
    if not header:
        return []
    if isinstance(header, dict):
        return [f"{key}: {value}" for key, value in header.items()]
    if isinstance(header, str):
        return [header]
    try:
        return [str(item) for item in header]
    except TypeError:
        return [str(header)]


def _event_mesh_amqp_ws_fallback_url() -> str:
    """Return configured SAP Event Mesh AMQP-over-WebSocket URL, if present."""
    # Prefer the per-thread context URL (per-instance) over process-global env.
    context_url = _context_url().strip()
    if context_url:
        lowered = context_url.lower()
        if "amqp10ws" in lowered or lowered.startswith(("wss://", "ws://")):
            return context_url
    for name in (
        "EVENT_MESH_MESSAGING_AMQP10WS_URL",
        "EVENT_MESH_AMQP10WS_URL",
        "EVENT_MESH_MESSAGING_URL",
        "EVENT_MESH_BROKER_URL",
    ):
        value = os.environ.get(name, "").strip()
        if not value:
            continue
        lowered = value.lower()
        if "amqp10ws" in lowered or lowered.startswith(("wss://", "ws://")):
            return value
    return ""


def _should_force_event_mesh_ws_url(raw_url: str, fallback_url: str) -> bool:
    """Decide whether to replace Azure's generated URL with SAP's configured URL."""
    if not fallback_url:
        return False

    raw = (raw_url or "").strip()
    lowered = raw.lower()

    if not raw:
        return True
    if "://none" in lowered or lowered.startswith("none") or "none:443" in lowered:
        return True

    if "amqp10ws" not in lowered and "amqp10ws" in fallback_url.lower():
        return True

    return False


def _normalize_subprotocols(
    subprotocols: list[str] | tuple[str, ...] | str | None,
) -> list[str]:
    if subprotocols is None:
        return [_DEFAULT_SUBPROTOCOL]
    if isinstance(subprotocols, str):
        return [subprotocols]
    normalized: list[str] = []
    for item in subprotocols:
        if item is None:
            continue
        if isinstance(item, bytes):
            normalized.append(item.decode("ascii", errors="ignore"))
        else:
            normalized.append(str(item))
    return normalized or [_DEFAULT_SUBPROTOCOL]


def _event_mesh_ws_origin(parsed: _ParsedWebSocketUrl) -> str:
    configured = os.environ.get("EVENT_MESH_AMQP_WS_ORIGIN", "").strip()
    if configured:
        return configured
    scheme = "https" if parsed.scheme == "wss" else "http"
    host = parsed.host
    if (parsed.scheme == "wss" and parsed.port != 443) or (
        parsed.scheme == "ws" and parsed.port != 80
    ):
        host = f"{host}:{parsed.port}"
    return f"{scheme}://{host}"


def _event_mesh_ws_handshake_timeout(default_timeout: float | None) -> float | None:
    configured = os.environ.get(
        "EVENT_MESH_AMQP_WS_HANDSHAKE_TIMEOUT_SECONDS", ""
    ).strip()
    if configured:
        try:
            value = float(configured)
            if value > 0:
                return value
        except ValueError:
            pass
    if default_timeout is not None and default_timeout > 0:
        return max(default_timeout, 30.0)
    return 30.0


def _event_mesh_ws_authorization_header() -> str:
    # Return a sanitized WebSocket Authorization header value.
    # Prefer the per-thread context (per-instance credentials) over the
    # process-global env vars so concurrent clients cannot clobber each other.
    explicit = (
        _context_authorization()
        or os.environ.get("EVENT_MESH_AMQP_WS_AUTHORIZATION", "").strip()
    )
    bearer = os.environ.get("EVENT_MESH_AMQP_WS_BEARER_TOKEN", "").strip()

    candidate = explicit or (f"Bearer {bearer}" if bearer else "")
    if not candidate:
        return ""

    if candidate.lower().startswith("bearer "):
        parts = candidate.split(None, 1)
        token = parts[1].strip() if len(parts) > 1 else ""
    else:
        token = candidate.strip()

    token = "".join(token.split())
    if token.lower().startswith("bearer"):
        # Recover from "BearerBearer..." or "Bearer Bearer ..." variants.
        token = token[6:].strip()
    if not token:
        return ""

    return f"Bearer {token}"


def _event_mesh_ws_authorization_diagnostic() -> str:
    value = _event_mesh_ws_authorization_header()
    if not value:
        return "authorization_sent=False"
    # SECURITY: this diagnostic is embedded in the failed-upgrade ConnectionError
    # message, which is logged upstream. It must therefore never emit anything
    # derived from the token itself — not its contents, not its length, and not
    # its JWT segment count — since all of those leak token material into logs.
    # Report only that a Bearer header was sent.
    configured = os.environ.get("EVENT_MESH_AMQP_WS_AUTH_DIAGNOSTIC", "").strip()
    base = "authorization_sent=True auth_scheme=Bearer"
    return f"{base} {configured}" if configured else base


class _StdlibSyncWebSocket:
    def __init__(
        self,
        url: str,
        *,
        timeout: float | None = None,
        sslopt: dict[str, Any] | None = None,
        subprotocols: list[str] | tuple[str, ...] | None = None,
        header: Any = None,
        **_: Any,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.sslopt = dict(sslopt or {})
        self.subprotocols = _normalize_subprotocols(subprotocols)
        self.header = header
        self.sock: socket.socket | ssl.SSLSocket | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self.sock is not None

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout
        if self.sock is not None:
            self.sock.settimeout(timeout)

    def connect(self) -> None:
        fallback_url = _event_mesh_amqp_ws_fallback_url()
        effective_url = (
            fallback_url
            if _should_force_event_mesh_ws_url(self.url, fallback_url)
            else self.url
        )
        parsed = _parse_url(effective_url)

        self.url = effective_url

        try:
            raw_sock = socket.create_connection(
                (parsed.host, parsed.port), self.timeout
            )
        except OSError as exc:
            raise OSError(
                "WebSocket TCP connect failed "
                f"url={self.url!r} parsed_host={parsed.host!r} "
                f"parsed_port={parsed.port!r} parsed_path={parsed.path!r}"
            ) from exc
        raw_sock.settimeout(self.timeout)

        if parsed.scheme == "wss":
            if self.sslopt.get("cert_reqs") == ssl.CERT_NONE:
                context = ssl._create_unverified_context()  # noqa: SLF001
            else:
                context = ssl.create_default_context(
                    cafile=self.sslopt.get("ca_certs"),
                )
            self.sock = context.wrap_socket(raw_sock, server_hostname=parsed.host)
        else:
            self.sock = raw_sock

        previous_timeout = None
        try:
            previous_timeout = self.sock.gettimeout() if self.sock is not None else None
            if self.sock is not None:
                self.sock.settimeout(_event_mesh_ws_handshake_timeout(self.timeout))
            self._handshake(parsed)
        except Exception:
            self.close()
            raise
        finally:
            if self.sock is not None:
                self.sock.settimeout(
                    previous_timeout if previous_timeout is not None else self.timeout
                )

        self._connected = True

    def _handshake(self, parsed: _ParsedWebSocketUrl) -> None:
        """Perform AMQP-over-WebSocket HTTP upgrade with safe diagnostics."""
        key = base64.b64encode(os.urandom(16)).decode("ascii")

        auth_helper = globals().get("_event_mesh_ws_authorization_header")
        authorization_header = auth_helper() if callable(auth_helper) else ""

        host_header = parsed.host
        if (parsed.scheme == "wss" and parsed.port != 443) or (
            parsed.scheme == "ws" and parsed.port != 80
        ):
            host_header = f"{parsed.host}:{parsed.port}"

        path = parsed.path or "/"
        subprotocol = (
            os.environ.get("EVENT_MESH_AMQP_WS_SUBPROTOCOL", "amqp").strip() or "amqp"
        )

        request_lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host_header}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
            f"Sec-WebSocket-Protocol: {subprotocol}",
            "User-Agent: simplemdg-agent-sdk-stdlib-websocket",
        ]

        origin_helper = globals().get("_event_mesh_ws_origin")
        origin = ""
        if callable(origin_helper):
            try:
                origin = origin_helper(parsed)
            except Exception:
                origin = ""
        if origin:
            request_lines.append(f"Origin: {origin}")

        if authorization_header:
            request_lines.append(f"Authorization: {authorization_header}")

        # Preserve caller-supplied non-core headers, but never duplicate the
        # core WebSocket/HTTP headers controlled above.
        raw_headers = (
            getattr(self, "header", None) or getattr(self, "headers", None) or []
        )
        if isinstance(raw_headers, dict):
            raw_iter = [f"{k}: {v}" for k, v in raw_headers.items()]
        elif isinstance(raw_headers, str):
            raw_iter = [raw_headers]
        else:
            raw_iter = list(raw_headers)

        blocked_prefixes = (
            "host:",
            "upgrade:",
            "connection:",
            "sec-websocket-key:",
            "sec-websocket-version:",
            "sec-websocket-protocol:",
            "authorization:",
            "origin:",
            "user-agent:",
        )
        for item in raw_iter:
            header = str(item).strip()
            if not header:
                continue
            if header.lower().startswith(blocked_prefixes):
                continue
            request_lines.append(header)

        # HTTP/1.1 requires CRLF between the request line/headers and a blank
        # CRLF after headers. Build it without escape literals so copy/paste or
        # patch transport cannot collapse it into an empty string.
        crlf = chr(13) + chr(10)
        request = crlf.join(request_lines) + crlf + crlf
        assert self.sock is not None
        self.sock.sendall(request.encode("ascii"))

        try:
            raw_response = self._read_http_response()
        except (TimeoutError, socket.timeout) as exc:
            raise TimeoutError(
                "WebSocket upgrade timed out waiting for HTTP response "
                f"url={self.url!r} path={path!r} host={parsed.host!r} "
                f"port={parsed.port!r} subprotocol={subprotocol!r} "
                f"authorization_sent={bool(authorization_header)}"
            ) from exc

        header_end = bytes([13, 10, 13, 10])
        alt_header_end = bytes([10, 10])
        head, sep, body = raw_response.partition(header_end)
        if not sep:
            head, sep, body = raw_response.partition(alt_header_end)

        lines = head.decode("iso-8859-1", errors="replace").splitlines()
        status_line = lines[0] if lines else ""
        response_headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            response_headers[name.strip().lower()] = value.strip()

        content_length = 0
        try:
            content_length = int(response_headers.get("content-length", "0"))
        except ValueError:
            content_length = 0

        while content_length and len(body) < content_length:
            try:
                chunk = self.sock.recv(min(4096, content_length - len(body)))
            except (TimeoutError, socket.timeout):
                break
            if not chunk:
                break
            body += chunk

        status_code = 0
        try:
            status_code = int(status_line.split()[1])
        except Exception:
            status_code = 0

        if status_code != 101:
            safe_headers = {
                k: (
                    "<redacted>"
                    if k in {"authorization", "set-cookie", "cookie"}
                    else v
                )
                for k, v in response_headers.items()
            }
            body_preview = body[:500].decode("utf-8", errors="replace")
            auth_diag_helper = globals().get("_event_mesh_ws_authorization_diagnostic")
            auth_diag = (
                auth_diag_helper()
                if callable(auth_diag_helper)
                else f"authorization_sent={bool(authorization_header)}"
            )
            raise ConnectionError(
                "WebSocket upgrade failed: "
                f"{status_line} url={self.url!r} path={path!r} "
                f"host={parsed.host!r} port={parsed.port!r} "
                f"subprotocol={subprotocol!r} {auth_diag} "
                f"response_headers={safe_headers!r} body_preview={body_preview!r}"
            )

        expected_accept = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()
        ).decode("ascii")
        actual_accept = response_headers.get("sec-websocket-accept", "")
        if actual_accept and actual_accept != expected_accept:
            raise ConnectionError(
                "WebSocket upgrade failed: invalid Sec-WebSocket-Accept "
                f"url={self.url!r} expected_len={len(expected_accept)} "
                f"actual_len={len(actual_accept)}"
            )

    def _read_http_response(self) -> bytes:
        chunks: list[bytes] = []
        header_end = bytes([13, 10, 13, 10])
        alt_header_end = bytes([10, 10])
        while True:
            if self.sock is None:
                raise ConnectionError("WebSocket socket closed during handshake")
            data = self.sock.recv(4096)
            if not data:
                raise ConnectionError("WebSocket closed during handshake")
            chunks.append(data)
            response = b"".join(chunks)
            if header_end in response or alt_header_end in response:
                return response

    def send(self, data: bytes | bytearray | memoryview | str) -> int:
        if isinstance(data, str):
            payload = data.encode("utf-8")
            opcode = 0x1
        else:
            payload = bytes(data)
            opcode = 0x2
        self._send_frame(payload, opcode=opcode)
        return len(payload)

    def send_binary(self, data: bytes | bytearray | memoryview) -> int:
        payload = bytes(data)
        self._send_frame(payload, opcode=0x2)
        return len(payload)

    def ping(self, payload: bytes | bytearray | memoryview = b"") -> None:
        self._send_frame(bytes(payload), opcode=0x9)

    def pong(self, payload: bytes | bytearray | memoryview = b"") -> None:
        self._send_frame(bytes(payload), opcode=0xA)

    def recv(self) -> bytes:
        fragments: list[bytes] = []
        current_opcode: int | None = None
        fragments_total = 0
        max_message_bytes = _websocket_max_bytes(
            "EVENT_MESH_AMQP_WS_MAX_MESSAGE_BYTES",
            _DEFAULT_MAX_MESSAGE_BYTES,
        )

        while True:
            fin, opcode, payload = self._read_frame()

            if opcode == 0x8:
                self.close()
                raise WebSocketConnectionClosedException("WebSocket closed by peer")

            if opcode == 0x9:
                self.pong(payload)
                continue

            if opcode == 0xA:
                continue

            if opcode in {0x1, 0x2}:
                if fin:
                    return payload
                current_opcode = opcode
                fragments = [payload]
                fragments_total = len(payload)
                if fragments_total > max_message_bytes:
                    self.close()
                    raise WebSocketProtocolError(
                        "WebSocket message exceeds configured maximum size"
                    )
                continue

            if opcode == 0x0:
                if current_opcode is None:
                    raise ConnectionError("Unexpected WebSocket continuation frame")
                fragments_total += len(payload)
                if fragments_total > max_message_bytes:
                    self.close()
                    raise WebSocketProtocolError(
                        "WebSocket fragmented message exceeds configured maximum size"
                    )
                fragments.append(payload)
                if fin:
                    combined = b"".join(fragments)
                    fragments = []
                    current_opcode = None
                    fragments_total = 0
                    return combined
                continue

            raise ConnectionError(f"Unsupported WebSocket opcode: {opcode}")

    def _send_frame(self, payload: bytes, *, opcode: int) -> None:
        if self.sock is None:
            raise WebSocketConnectionClosedException(
                "WebSocket socket is not connected"
            )
        if opcode in {0x8, 0x9, 0xA} and len(payload) > 125:
            raise ValueError("WebSocket control frame payload cannot exceed 125 bytes")

        first = 0x80 | opcode
        length = len(payload)
        mask_key = os.urandom(4)

        if length < 126:
            header = struct.pack("!BB", first, 0x80 | length)
        elif length <= 0xFFFF:
            header = struct.pack("!BBH", first, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", first, 0x80 | 127, length)

        masked = bytes(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(header + mask_key + masked)

    def _read_frame(self) -> tuple[bool, int, bytes]:
        header = self._read_exact(2)
        first, second = header[0], header[1]
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F

        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]

        max_frame_bytes = _websocket_max_bytes(
            "EVENT_MESH_AMQP_WS_MAX_FRAME_BYTES",
            _DEFAULT_MAX_FRAME_BYTES,
        )
        if length > max_frame_bytes:
            self.close()
            raise WebSocketProtocolError(
                "WebSocket frame exceeds configured maximum size"
            )

        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length) if length else b""
        if masked:
            payload = bytes(
                byte ^ mask[index % 4] for index, byte in enumerate(payload)
            )
        return fin, opcode, payload

    def _read_exact(self, size: int) -> bytes:
        if size == 0:
            return b""
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            if self.sock is None:
                raise WebSocketConnectionClosedException(
                    "WebSocket socket is not connected"
                )
            chunk = self.sock.recv(remaining)
            if not chunk:
                self.close()
                raise WebSocketConnectionClosedException(
                    "WebSocket closed while reading"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def close(self) -> None:
        sock = self.sock
        self.sock = None
        self._connected = False
        if sock is None:
            return
        try:
            payload = b""
            first = 0x80 | 0x8
            mask_key = os.urandom(4)
            frame = struct.pack("!BB", first, 0x80 | len(payload)) + mask_key
            sock.sendall(frame)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass


# SAP Event Mesh compatibility wrapper.
# Azure _pyamqp sometimes reaches this stdlib shim with a host-only URL even
# though SAP requires /protocols/amqp10ws for the WebSocket upgrade. Prefer the
# explicit SAP AMQP WS endpoint from configuration when the parsed URL has no
# useful path, or when previous transport composition lost the host.
_ORIGINAL_PARSE_URL_FOR_SAP_EVENT_MESH = _parse_url


def _parse_url(raw_url: str) -> _ParsedWebSocketUrl:  # type: ignore[no-redef]
    parsed = _ORIGINAL_PARSE_URL_FOR_SAP_EVENT_MESH(raw_url)

    fallback = _event_mesh_amqp_ws_fallback_url()
    if not fallback:
        return parsed

    fallback_parsed = _ORIGINAL_PARSE_URL_FOR_SAP_EVENT_MESH(fallback)
    raw_lower = (raw_url or "").strip().lower()

    lost_host = (
        not parsed.host
        or parsed.host.lower() in {"none", "null"}
        or "://none" in raw_lower
        or "none:443" in raw_lower
    )
    missing_path = parsed.path in {"", "/"}
    fallback_has_ws_path = (
        fallback_parsed.path not in {"", "/"}
        and "amqp10ws" in fallback_parsed.path.lower()
    )

    if lost_host or (missing_path and fallback_has_ws_path):
        return fallback_parsed

    return parsed


def create_connection(
    url: str,
    timeout: float | None = None,
    sslopt: dict[str, Any] | None = None,
    subprotocols: list[str] | tuple[str, ...] | None = None,
    header: Any = None,
    **kwargs: Any,
) -> _StdlibSyncWebSocket:
    websocket = _StdlibSyncWebSocket(
        url,
        timeout=timeout,
        sslopt=sslopt,
        subprotocols=subprotocols,
        header=header,
        **kwargs,
    )
    websocket.connect()
    return websocket


StdlibSyncWebSocket = _StdlibSyncWebSocket
_StdlibWebSocket = _StdlibSyncWebSocket

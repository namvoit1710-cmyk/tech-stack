"""Push-based AMQP 1.0 consumer for SAP Event Mesh (proton engine + WebSocket).

Why this shape
--------------
No off-the-shelf Python library does AMQP-1.0-over-WebSocket against SAP Event
Mesh: ``uamqp`` does WebSocket but only Azure-style CBS auth (401s on SAP), and
``python-qpid-proton`` does SASL PLAIN (SAP's mechanism) but has **no WebSocket
transport at all** (only ``amqp://`` / ``amqps://``). So we bridge the two
working halves:

* **websocket-client** establishes the ``wss://`` connection — TLS, the
  WebSocket upgrade, the ``amqp`` sub-protocol, and an ``Authorization: Bearer``
  header on the upgrade (SAP gates the handshake — this is what 401'd uamqp).
* **proton's low-level engine** (``Transport`` / ``Connection`` / ``Receiver``)
  does AMQP 1.0 framing + **SASL PLAIN** (OAuth token as the password). It's
  decoupled from IO: we feed it bytes with ``transport.push`` and drain bytes
  with ``transport.peek``/``pop``, pumping them across the WebSocket.

Scope unchanged: consume path only; same ``EventMeshAMQPConsumer`` interface so
the broker is untouched; falls back to REST when the libs are missing or the
endpoint can't be reached. The whole bridge runs under a broad ``try`` so any
proton/websocket API mismatch degrades to that REST fallback, never a crash.

SAP-specific knobs (validate against your service key)
------------------------------------------------------
* URL ``wss://<host>:<port><ws_path>`` (ws_path = ``/protocols/amqp10ws``)
* WebSocket sub-protocol ``amqp``; bearer token on the upgrade + as SASL password
* ``queue:<name>`` link address; ``strip_namespace`` drops the namespace prefix
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Both libs are optional at runtime: the service must still boot (and fall back
# to REST) without them.
try:  # pragma: no cover - availability depends on the deploy image
    import websocket  # websocket-client

    from proton import Collector, Connection, Delivery, Event, Message, Transport

    _AMQP_IMPORT_ERROR: Optional[Exception] = None
except Exception as _exc:  # pragma: no cover
    websocket = None  # type: ignore[assignment]
    Collector = Connection = Delivery = Event = Message = Transport = None  # type: ignore[assignment]
    _AMQP_IMPORT_ERROR = _exc


def amqp_available() -> bool:
    """Return True if both proton and websocket-client imported cleanly."""
    return websocket is not None and Connection is not None


def amqp_import_error() -> Optional[Exception]:
    """Return the import error if the AMQP libs are unavailable, else ``None``."""
    return _AMQP_IMPORT_ERROR


# Reconnect backoff bounds after a connection drop.
_RECONNECT_BASE_S = 1.0
_RECONNECT_MAX_S = 30.0

# How long teardown may spend flushing the AMQP detach + close frames before
# the socket is dropped anyway. Small: the frames are a few hundred bytes and
# nothing is read back — this only bounds an unresponsive peer.
_CLOSE_FLUSH_S = 1.0

# Credit granted by the *probe* link. The probe only has to prove the link
# attaches; it must never become a consumer, because it stops pumping the
# moment it is ready. 1 is the floor ``_ProtonWsBridge`` allows, and combined
# with the proper close in ``_close`` it bounds the blast radius of a probe to
# at most one in-flight message even if the detach is lost.
_PROBE_PREFETCH = 1

# How long the probe holds an attached link open before declaring success. A
# broker that refuses the address attaches first and detaches a few
# milliseconds later, so returning on the attach echo alone reports OK on a
# dead subscription. Observed rejection latency on Event Mesh: <150 ms.
_PROBE_CONFIRM_S = 0.5

# A receive session must stay attached at least this long before its backoff is
# reset. Without it, a link that is attached-then-immediately-rejected looks
# "healthy" to the reconnect loop and it spins: ~13 reconnects in 1.7s with no
# backoff, observed 2026-08-05 against a deleted queue.
_HEALTHY_SESSION_S = 5.0

# Log every Nth delivery per link. The first is always logged; after that
# this keeps a busy queue from flooding the log while still proving the
# link is pulling.
_DELIVERY_LOG_EVERY = 100
# Back off the stall threshold after repeated forced reconnects so a queue that
# genuinely cannot deliver doesn't reconnect-storm the broker.
_STALL_BACKOFF_MAX = 8
# Bounded connect probe used for auto-fallback to REST at subscribe time.
_DEFAULT_PROBE_TIMEOUT_S = 15.0
# WebSocket recv timeout (s) — bounds the pump loop so it stays responsive to
# shutdown and can flush proton's outgoing frames / heartbeats promptly.
_WS_IO_TIMEOUT_S = 1.0
# SASL PLAIN authcid — SAP Event Mesh ignores it but it must be non-empty.
_SASL_USERNAME = "token"
_WS_SUBPROTOCOL = "amqp"
# SASL mechanisms the client offers. SAP Event Mesh authenticates the *WebSocket
# upgrade* via the bearer token, so on the AMQP layer it offers SASL ANONYMOUS
# (no second auth). uamqp-style PLAIN-only got PN_SASL_PERM (no common mech).
# Space-separated; proton negotiates whichever the server also offers. Override
# via EVENT_MESH_AMQP_SASL_MECHS if a plan offers PLAIN/EXTERNAL instead.
_SASL_MECHS = os.getenv("EVENT_MESH_AMQP_SASL_MECHS", "ANONYMOUS PLAIN")


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    """Read a bounded int from the environment, tolerating garbage.

    A malformed value must not stop the service from booting — this module is
    on the startup path and its only alternative is the REST fallback.
    """
    raw = os.getenv(name, "")
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        logger.warning("%s=%r is not an integer — using %d", name, raw, default)
        return max(minimum, default)


# Bounds for the AMQP handshake diagnostic (M2 — SA-2259, recurrence of SA-1861).
_EVENT_LOG_HEAD = _int_env("EVENT_MESH_AMQP_EVENT_LOG_HEAD", 32)
_EVENT_LOG_TAIL = _int_env("EVENT_MESH_AMQP_EVENT_LOG_TAIL", 32, minimum=1)
# Event names come from a small fixed proton enum, but an unexpected
# ``str(etype)`` must not turn the tally into a second unbounded container.
_EVENT_LOG_KINDS = _int_env("EVENT_MESH_AMQP_EVENT_LOG_KINDS", 64, minimum=1)
_EVENT_LOG_OTHER = "OTHER"

# Reconnect this long before the SASL token expires. Measured on tenant-1
# 2026-08-05: the token is valid for 12 hours, so a healthy link sails past
# expiry roughly once a day. ``_receive_loop`` already takes a fresh token on
# every reconnect — what was missing is a reason to reconnect before the
# credential the link authenticated with goes stale underneath it.
_TOKEN_RECONNECT_MARGIN_S = _int_env("EVENT_MESH_AMQP_TOKEN_MARGIN_S", 300, minimum=30)
# Liveness watchdog. ``_receive_loop`` only reconnects when ``run()`` returns,
# i.e. on disconnect — an attached-but-SILENT link never returns, so the loop
# could spin forever consuming nothing. That is exactly what the three tenant-1
# AMQP outages looked like: six links OPEN, zero deliveries, no errors, no
# detaches, and nothing in the system able to see it.
_STALL_CHECK_INTERVAL_S = _int_env("EVENT_MESH_AMQP_STALL_CHECK_S", 30, minimum=5)
_STALL_AFTER_S = _int_env("EVENT_MESH_AMQP_STALL_AFTER_S", 90, minimum=10)
# Largest AMQP frame proton will accept. Left unset, proton's default is far
# below a real WCP payload and the transport kills the link mid-message with
# ``unable to process all bytes: <offered>, <processed>``. Measured on tenant-1
# 2026-08-05: 31 reconnects in ~7 minutes on exactly the three large-payload
# queues (workflow.triggers 14, realtime.events 12, workflow.results 5), while
# orchestration.actions — small messages — never flapped once. The publisher
# caps a payload at 900 KB, so 1 MiB covers every message we can send.
_MAX_FRAME_BYTES = _int_env("EVENT_MESH_AMQP_MAX_FRAME_BYTES", 1_048_576, minimum=65_536)


class _BoundedEventLog:
    """A fixed-size record of proton event names for the handshake diagnostic.

    ``events_seen`` used to be a plain ``list`` appended once per proton event
    and never trimmed. On the *probe* path the bridge is short-lived, so it
    stayed small and the leak was invisible; on the *receive* path the bridge
    lives as long as the connection, so the list grew with traffic for the life
    of the pod. Its sole reader is the probe-failure log line, which exists to
    answer one question: how far did the handshake get?

    A plain ``deque(maxlen=N)`` bounds the memory but discards the **head** —
    and the head is exactly what answers that question (an empty log, or the
    first CONNECTION / SESSION / LINK events). Dropping it would trade a leak
    for a blind spot. So both ends are kept: an immutable handshake prefix and
    a sliding window of the most recent events, with a per-name tally covering
    what fell in between.

    Every container here has a ceiling — prefix, window, and the tally's key
    space — so the memory is O(head + tail + kinds) regardless of traffic.
    """

    __slots__ = ("_head", "_tail", "_counts", "_total", "_head_max", "_kinds_max")

    def __init__(self, head: int | None = None, tail: int | None = None,
                 kinds: int | None = None) -> None:
        self._head_max = _EVENT_LOG_HEAD if head is None else max(0, int(head))
        self._kinds_max = _EVENT_LOG_KINDS if kinds is None else max(1, int(kinds))
        tail_max = _EVENT_LOG_TAIL if tail is None else max(1, int(tail))
        self._head: list[str] = []
        self._tail: deque[str] = deque(maxlen=tail_max)
        self._counts: Counter[str] = Counter()
        self._total = 0

    def append(self, name: str) -> None:
        """Record one event. O(1) time, and no growth past the configured caps."""
        self._total += 1
        if name in self._counts or len(self._counts) < self._kinds_max:
            self._counts[name] += 1
        else:
            self._counts[_EVENT_LOG_OTHER] += 1
        if len(self._head) < self._head_max:
            self._head.append(name)
        else:
            self._tail.append(name)

    def clear(self) -> None:
        """Release everything — used when a bridge is torn down and reconnects."""
        self._head.clear()
        self._tail.clear()
        self._counts.clear()
        self._total = 0

    @property
    def total(self) -> int:
        """Every event ever seen, including the ones no longer retained."""
        return self._total

    def counts(self) -> dict[str, int]:
        """Per-name tally, capped at ``kinds`` distinct keys."""
        return dict(self._counts)

    def __len__(self) -> int:
        return self._total

    def __iter__(self):
        return iter(self._head + list(self._tail))

    def __contains__(self, name: object) -> bool:
        return name in self._head or name in self._tail

    def __eq__(self, other: object) -> bool:
        # Compare equal to the plain list this replaced, so callers and tests
        # that were written against the old type keep working.
        if isinstance(other, _BoundedEventLog):
            return list(self) == list(other) and self._total == other._total
        if isinstance(other, (list, tuple)):
            return list(self) == list(other)
        return NotImplemented

    def __repr__(self) -> str:
        if not self._tail:
            return repr(self._head)
        elided = self._total - len(self._head) - len(self._tail)
        middle = [f"...{elided} more..."] if elided > 0 else []
        return repr(self._head + middle + list(self._tail))

    __str__ = __repr__


def _parse_body(message: Any) -> dict:
    """Decode a proton message body into a Python object (JSON-or-raw tolerant)."""
    body = getattr(message, "body", None)
    # proton returns an AMQP data section as a memoryview — get the bytes out.
    if isinstance(body, memoryview):
        body = body.tobytes()
    if isinstance(body, (bytes, bytearray)):
        text: Any = bytes(body).decode("utf-8")
    elif isinstance(body, str):
        text = body
    elif isinstance(body, dict):
        return body
    else:
        return {"data": body}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {"raw": text}


class _ProtonWsBridge:
    """One WebSocket + proton-engine receiver link for a single queue.

    Pumps bytes between a websocket-client connection and a proton ``Transport``
    until the connection closes, ``is_running`` goes false, or (probe mode) the
    link opens / a deadline passes.
    """

    def __init__(
        self,
        *,
        url: str,
        address: str,
        queue_name: str,
        token: str,
        prefetch: int,
        message_handler: Callable[[str, dict], bool],
        is_running: Callable[[], bool],
        hostname: str = "",
        expires_at: float = 0.0,
        epoch: int = 0,
    ) -> None:
        self._url = url
        self._address = address
        self._queue_name = queue_name
        self._token = token
        # Epoch seconds at which ``token`` stops being valid; 0 = unknown.
        self._expires_at = float(expires_at or 0.0)
        self._prefetch = max(1, int(prefetch))
        self._message_handler = message_handler
        self._is_running = is_running
        self._hostname = hostname

        self._ws: Any = None
        self._conn: Any = None
        self._transport: Any = None
        self._collector: Any = None
        self._receiver: Any = None
        self._closed = False
        self.ready = False  # link attached AND the peer echoed a real source
        self.attached_at = 0.0  # monotonic time ``ready`` flipped; 0 = never
        self.deliveries = 0  # messages this link has actually received
        # Identifies THIS bridge to the watchdog. ``deliveries`` restarts at 0
        # on every reconnect, so the watchdog has to be able to tell a fresh
        # bridge from the one whose count it last recorded — otherwise it
        # compares a new counter against an old high-water mark and reads a
        # perfectly healthy link as silent. An int, not the object, so nothing
        # holds a dead bridge (and its WebSocket) alive.
        self.epoch = int(epoch)
        self.error: Optional[BaseException] = None
        # Set when the bridge stood down on purpose (token rotation, watchdog).
        # Distinct from an error so the reconnect loop doesn't back off.
        self.stood_down = ""
        # Diagnostics — show exactly how far the AMQP handshake got. Bounded:
        # a receive-path bridge lives as long as the connection, so a plain
        # list here grew for the life of the pod (M2 — SA-2259 / SA-1861).
        self.events_seen = _BoundedEventLog()
        self.bytes_sent = 0
        self.bytes_recv = 0
        self.sasl_outcome: Any = None

    # -- setup -----------------------------------------------------------------

    def _open(self) -> None:
        # WebSocket: TLS + upgrade + amqp sub-protocol + bearer on the handshake.
        self._ws = websocket.create_connection(
            self._url,
            subprotocols=[_WS_SUBPROTOCOL],
            header=[f"Authorization: Bearer {self._token}"],
            timeout=_WS_IO_TIMEOUT_S,
            enable_multithread=True,
        )
        self._ws.settimeout(_WS_IO_TIMEOUT_S)

        # proton engine: SASL PLAIN with the token as the password.
        self._conn = Connection()
        self._conn.user = _SASL_USERNAME
        self._conn.password = self._token
        # AMQP open.hostname — SAP's gateway may need it to route to the
        # service-instance/namespace. Defaults to the gateway host.
        if self._hostname:
            try:
                self._conn.hostname = self._hostname
            except Exception:
                pass
        self._transport = Transport()
        # MUST be set before bind: proton negotiates the frame size during the
        # AMQP open, so setting it afterwards is a silent no-op.
        #
        # This caps what the *peer* may send us in one frame. It is only half
        # the protection — our own input buffer is bounded separately, and
        # over-filling it is what actually killed the large-payload queues
        # (see ``_feed``). Raising this alone did not fix them; it only moved
        # the capacity from 99 to 7973 bytes.
        try:
            self._transport.max_frame_size = _MAX_FRAME_BYTES
        except Exception:  # pragma: no cover — never block the connection over a tunable
            logger.warning("Could not set AMQP max_frame_size (queue=%s)", self._queue_name)
        self._transport.bind(self._conn)
        sasl = self._transport.sasl()
        # The WebSocket already provides TLS; proton sees a plain byte stream and
        # would otherwise refuse PLAIN as insecure.
        sasl.allow_insecure_mechs = True
        sasl.allowed_mechs(_SASL_MECHS)
        self._collector = Collector()
        self._conn.collect(self._collector)

        self._conn.open()
        session = self._conn.session()
        session.open()
        self._receiver = session.receiver(f"recv-{self._queue_name}")
        self._receiver.source.address = self._address
        self._receiver.open()
        self._pump_out()  # flush AMQP header + SASL init + open frames

    def _close(self) -> None:
        """Detach the link and close the connection *before* dropping the socket.

        Closing the WebSocket under a live link leaves the broker holding a
        subscription that still has outstanding credit. It keeps routing
        messages to a consumer that will never settle them; those messages
        return to the queue after the lock TTL, and a *push* receiver never
        comes back for them — push only delivers what the broker dispatches
        next. They are stranded.

        Measured on a scratch queue, 2026-08-05: ``probe()`` opens its own
        connection, returns the moment the link is ready, and dropped the
        socket here. Of 20 messages published afterwards, **3 never arrived**
        (2 found back in the queue, 1 still locked) while seqs 3-19 delivered
        cleanly. On an orchestration queue one stranded ``StartRunAction`` is
        a permanently hung run — which is exactly what enabling AMQP on
        tenant-1 produced.

        Bounded: a peer that never answers must not wedge teardown, so the
        frames are flushed best-effort and the socket closes regardless.
        """
        try:
            if self._receiver is not None:
                self._receiver.close()
            if self._conn is not None:
                self._conn.close()
            # Flush the detach + close frames. Nothing is read back — the
            # frames only need to reach the broker for it to release the
            # subscription and its credit.
            deadline = time.monotonic() + _CLOSE_FLUSH_S
            while time.monotonic() < deadline:
                self._pump_out()
                if self._transport is None or self._transport.pending() <= 0:
                    break
                time.sleep(0.01)
        except Exception:
            # A transport already torn down by the peer raises here; the
            # socket close below is still the right next step.
            pass
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    # -- pump ------------------------------------------------------------------

    def _pump_out(self) -> None:
        """Drain proton's outgoing bytes and write them to the WebSocket."""
        while True:
            pending = self._transport.pending()
            if pending > 0:
                data = self._transport.peek(pending)
                if data:
                    self._ws.send_binary(data)
                    self._transport.pop(len(data))
                    self.bytes_sent += len(data)
                else:
                    break
            elif pending == 0:
                break
            else:  # pending < 0 → transport closed
                self._closed = True
                break

    def _pump_in(self) -> bool:
        """Read one WebSocket frame into proton. Returns False if the WS closed."""
        try:
            data = self._ws.recv()
        except Exception as exc:  # timeout (expected) or a real WS error
            if isinstance(exc, getattr(websocket, "WebSocketTimeoutException", ())):
                return True
            self.error = exc
            return False
        if data is None or data == "" or data == b"":
            return False  # peer closed
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not data:
            return True
        return self._feed(data)

    def _feed(self, data: bytes) -> bool:
        """Hand ``data`` to proton in ``capacity()``-sized chunks.

        ``Transport.push`` is not a queue. Proton holds an input buffer of
        ``capacity()`` bytes, and the Python binding raises
        ``unable to process all bytes: <consumed>, <offered>`` the instant it
        is handed more than that — *after* consuming part of the frame, so the
        remainder is lost and the AMQP byte stream is desynchronised. There is
        no recovering a link that has missed bytes mid-frame.

        The bridge used to push whole WebSocket frames, which is only safe
        while every frame happens to be smaller than the buffer. Measured on
        tenant-1 2026-08-05 (``solace-970``): a 14039-byte WebSocket frame
        against 7973 bytes of capacity killed
        ``sub-workflow-triggers-amqp`` — the Jira queue, the one with the
        large payloads — 0.1s after every attach, seven times in a row, with
        **zero** deliveries in between. The small-payload queues never flapped
        once.

        One WebSocket frame is not one AMQP frame: the gateway coalesces and
        splits as it likes, so the frame size is the peer's choice while the
        capacity is ours. Chunking is what makes the two independent —
        ``max_frame_size`` bounds what the peer may send, this bounds what we
        hand over at a time, and only both together are safe.

        Returns False if the transport died; the caller reconnects.
        """
        offset, total = 0, len(data)
        while offset < total:
            capacity = self._transport.capacity()
            if capacity == 0:
                # Full. Let proton decode what it already holds — that is what
                # frees the buffer — and flush anything the decode generated.
                self._process_events()
                self._pump_out()
                if self._closed:
                    # Torn down while draining (peer close, or a handler stood
                    # the link down). Nothing left to feed it.
                    return True
                capacity = self._transport.capacity()
            if capacity < 0:  # PN_EOS — transport gone
                self._closed = True
                return False
            if capacity == 0:
                # Draining freed nothing, so proton is holding a partial frame
                # it cannot complete: the peer sent a frame larger than the
                # buffer, i.e. it ignored our advertised max_frame_size.
                # Silently dropping the rest would desynchronise the stream,
                # so fail the link and let the supervisor reconnect.
                self.error = RuntimeError(
                    f"AMQP input buffer stuck at 0 capacity with {total - offset} "
                    f"of {total} bytes unread — peer frame exceeds "
                    f"max_frame_size={_MAX_FRAME_BYTES}"
                )
                return False
            chunk = data[offset:offset + capacity]
            self._transport.push(chunk)
            offset += len(chunk)
            self.bytes_recv += len(chunk)
            if offset < total:
                # Drain between chunks so the next capacity() reading reflects
                # a buffer proton has had the chance to empty.
                self._process_events()
                self._pump_out()
                if self._closed:
                    return True
        return True

    def _process_events(self) -> None:
        ev = self._collector.peek()
        while ev is not None:
            self._handle_event(ev)
            self._collector.pop()
            ev = self._collector.peek()

    def _handle_event(self, ev: Any) -> None:
        etype = ev.type
        name = getattr(etype, "name", str(etype))
        self.events_seen.append(name)
        if etype == Event.LINK_REMOTE_OPEN:
            if ev.receiver is not None:
                # An attach echo is NOT a subscription. AMQP 1.0 refuses a
                # source by echoing the attach with a NULL source and then
                # detaching, so a peer that is about to reject us still fires
                # this event first. Treating it as success is what let
                # ``probe()`` report OK on a dead subscription, and — because
                # ``_receive_loop`` resets its backoff whenever ``ready`` is
                # true — what let a rejected link reconnect ~13 times in 1.7s
                # with no backoff at all (observed 2026-08-05 against a
                # deleted queue).
                if not self._remote_source_ok(ev.receiver):
                    logger.warning(
                        "AMQP link attached with NO remote source (queue=%s) — "
                        "the broker is refusing this address; treating as failure",
                        self._queue_name,
                    )
                    self.error = RuntimeError(
                        f"attach refused: null remote source for {self._address}"
                    )
                    self._closed = True
                    return
                logger.info("AMQP receiver link OPEN (queue=%s)", self._queue_name)
                ev.receiver.flow(self._prefetch)
                self.attached_at = time.monotonic()
                self.ready = True
        elif etype == Event.DELIVERY:
            self._on_delivery(ev)
        elif etype in (Event.CONNECTION_REMOTE_OPEN, Event.SESSION_REMOTE_OPEN):
            # Lifecycle progress markers — show how far the handshake got.
            logger.info("AMQP %s (queue=%s)", name, self._queue_name)
        elif etype in (
            Event.LINK_REMOTE_CLOSE,
            Event.LINK_REMOTE_DETACH,
            Event.CONNECTION_REMOTE_CLOSE,
            Event.TRANSPORT_ERROR,
            Event.TRANSPORT_CLOSED,
        ):
            self._note_error(ev, name)
            self._closed = True
            # A rejection that lands AFTER the attach echo must un-set
            # ``ready``. Leaving it true is what made ``probe()`` return
            # success on a subscription the broker had already cancelled, and
            # what made the reconnect loop treat the session as healthy.
            if etype in (Event.LINK_REMOTE_CLOSE, Event.LINK_REMOTE_DETACH):
                if self.ready and time.monotonic() - self.attached_at < _PROBE_CONFIRM_S:
                    self.ready = False

    @staticmethod
    def _remote_source_ok(receiver: Any) -> bool:
        """True when the peer echoed a real source, i.e. it accepted the attach.

        A refusal comes back as an attach whose ``remote_source`` is absent or
        has no address. Anything we cannot introspect is treated as OK — this
        guard must never turn a working link into a false negative.
        """
        try:
            source = receiver.remote_source
        except Exception:
            return True
        if source is None:
            return False
        try:
            return bool(source.address)
        except Exception:
            return True

    def _on_delivery(self, ev: Any) -> None:
        # Deliveries were entirely silent. When AMQP was enabled on tenant-1
        # and every run hung, there was no way to tell whether the broker had
        # delivered nothing or the messages had died further down — the log
        # showed six links OPEN and then nothing at all. One line per queue
        # answers that, and the periodic one shows the link is still pulling.
        self.deliveries += 1
        if self.deliveries == 1:
            logger.info(
                "AMQP FIRST delivery (queue=%s) — the link is live", self._queue_name,
            )
        elif self.deliveries % _DELIVERY_LOG_EVERY == 0:
            logger.info(
                "AMQP delivered %d messages (queue=%s)",
                self.deliveries, self._queue_name,
            )
        dlv = ev.delivery
        rcv = ev.receiver
        if dlv is None or rcv is None:
            return
        if not dlv.readable or dlv.partial:
            return  # wait for the full message
        chunk = rcv.recv(dlv.pending)
        ok = True
        if chunk is not None:
            msg = Message()
            try:
                msg.decode(chunk)
                body = _parse_body(msg)
            except Exception:
                logger.exception("AMQP decode error (queue=%s) — accepting to drop", self._queue_name)
                body = None
            if body is not None:
                try:
                    ok = bool(self._message_handler(self._queue_name, body))
                except Exception:
                    logger.exception("AMQP handler error (queue=%s)", self._queue_name)
                    ok = False
        try:
            dlv.update(Delivery.ACCEPTED if ok else Delivery.RELEASED)
            dlv.settle()
            if not ok:
                logger.warning(
                    "AMQP handler failure (queue=%s) — released for redelivery",
                    self._queue_name,
                )
            rcv.flow(1)  # credit for the next message
        except Exception:
            logger.exception("AMQP settle error (queue=%s)", self._queue_name)
        self._pump_out()  # flush the disposition + credit promptly

    def _note_error(self, ev: Any, etype: Any) -> None:
        cond = None
        for obj in (getattr(ev, "link", None), getattr(ev, "connection", None), getattr(ev, "transport", None)):
            cond = getattr(obj, "remote_condition", None) or getattr(obj, "condition", None)
            if cond is not None:
                break
        if self.error is None and cond is not None:
            self.error = RuntimeError("%s: %s" % (etype, getattr(cond, "description", cond)))
        logger.warning("AMQP %s (queue=%s): %s", etype, self._queue_name, cond)

    # -- run -------------------------------------------------------------------

    def _token_expiring(self) -> bool:
        """True once the SASL token is within the reconnect margin of expiry."""
        if not self._expires_at:
            return False
        return time.time() >= self._expires_at - _TOKEN_RECONNECT_MARGIN_S

    def request_stop(self, reason: str) -> None:
        """Ask the pump loop to stand down (watchdog / shutdown).

        Sets the same flag the transport uses, so the loop exits within one
        WebSocket read timeout and ``_close`` still performs the orderly
        detach — dropping the socket under a live link strands whatever the
        broker had already routed to it.
        """
        self.stood_down = reason
        self._closed = True

    def run(self, deadline: Optional[float] = None) -> None:
        """Open and pump until closed / stopped / (probe) deadline reached."""
        try:
            self._open()
            while self._is_running() and not self._closed:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                # Stand down before the credential this link authenticated
                # with expires. Probes (deadline set) are far too short-lived
                # to care.
                if deadline is None and self._token_expiring():
                    self.stood_down = "token_expiry"
                    logger.info(
                        "AMQP standing down before token expiry (queue=%s) — "
                        "reconnecting with a fresh token", self._queue_name,
                    )
                    break
                if deadline is not None and self.ready:
                    # Probe: do NOT declare victory the instant the attach is
                    # echoed. A broker that is going to reject the address
                    # sends its detach a few milliseconds later, so hold the
                    # link open briefly and let ``_handle_event`` see it. If a
                    # rejection lands, ``_closed`` is set and the loop exits
                    # with ``ready`` already cleared by the detach handler.
                    if time.monotonic() - self.attached_at >= _PROBE_CONFIRM_S:
                        break  # attached, and still attached — probe satisfied
                self._pump_out()
                if self._closed:
                    break
                if not self._pump_in():
                    break
                self._process_events()
                self._pump_out()
                try:
                    self._transport.tick(time.time())  # heartbeats
                except Exception:
                    pass
        except Exception as exc:
            self.error = exc
        finally:
            # SASL outcome: 0=ok, 1=auth-failed (token rejected), -1/None=never
            # completed. The single most useful signal when the link stalls.
            try:
                if self._transport is not None:
                    self.sasl_outcome = self._transport.sasl().outcome
            except Exception:
                pass
            self._close()


class EventMeshAMQPConsumer:
    """Manages WebSocket+proton AMQP receivers for a set of Event Mesh queues.

    Transport-only: the broker supplies ``message_handler(queue, data) -> bool``
    and ``get_token`` (the token is used both on the WS upgrade and as the SASL
    PLAIN password).
    """

    def __init__(
        self,
        *,
        host: str,
        get_token: Callable[[], tuple[str, float]],
        message_handler: Callable[[str, dict], bool],
        port: int = 443,
        ws_path: str = "/protocols/amqp10ws",
        prefetch: int = 1,
        concurrency: int = 1,
        strip_namespace: bool = False,
        namespace: str = "",
        queue_depth: Optional[Callable[[str], Optional[int]]] = None,
        metrics: Any = None,
        probe_timeout_s: float = _DEFAULT_PROBE_TIMEOUT_S,
    ) -> None:
        self._host = host
        self._port = port
        self._ws_path = ws_path
        self._get_token = get_token
        self._message_handler = message_handler
        self._prefetch = max(1, int(prefetch))
        self._concurrency = max(1, int(concurrency))
        self._strip_namespace = strip_namespace
        self._namespace = namespace.rstrip("/")
        self._probe_timeout_s = max(1.0, float(probe_timeout_s))

        self._queue_depth = queue_depth
        self._metrics = metrics
        self._lock = threading.Lock()
        self._queues: set[str] = set()
        self._threads: list[threading.Thread] = []
        self._running = False
        # Live bridge per LINK — the watchdog's only window into whether a link
        # that says it is attached is actually receiving anything.
        #
        # Keyed by ``(queue_name, index)``, NOT by queue: ``concurrency`` opens
        # that many links onto the SAME queue, so a queue-keyed dict would let
        # them overwrite each other and only the last one registered would ever
        # be watched — the other N-1 could go silent, which is the precise
        # failure this watchdog exists to catch.
        self._bridges: dict[tuple[str, int], _ProtonWsBridge] = {}
        # link -> [last delivery count, stalled_since or None, forced streak, epoch]
        self._liveness: dict[tuple[str, int], list] = {}
        self._epoch = 0
        self.forced_reconnects = 0

    def _next_epoch(self) -> int:
        with self._lock:
            self._epoch += 1
            return self._epoch

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _url(self) -> str:
        return f"wss://{self._host}:{self._port}{self._ws_path}"

    def _source_address(self, queue_name: str) -> str:
        name = queue_name
        if self._strip_namespace and self._namespace:
            prefix = self._namespace + "/"
            if name.startswith(prefix):
                name = name[len(prefix):]
        return f"queue:{name}"

    def _token(self) -> str:
        token, _expires_at = self._get_token()
        return token

    def _token_with_expiry(self) -> tuple[str, float]:
        """Token AND its expiry.

        ``EventMeshTokenProvider.amqp_token`` deliberately returns the true
        expiry; dropping it here is what let a link outlive the credential it
        authenticated with. Measured 2026-08-05: the token is good for 12
        hours, so a healthy link crosses expiry about once a day.
        """
        token, expires_at = self._get_token()
        try:
            return token, float(expires_at or 0.0)
        except (TypeError, ValueError):
            return token, 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def add_queue(self, queue_name: str) -> None:
        with self._lock:
            if queue_name in self._queues:
                return
            self._queues.add(queue_name)
            if self._running:
                self._spawn_queue_threads(queue_name)

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            for queue_name in self._queues:
                self._spawn_queue_threads(queue_name)
            if self._queue_depth is not None:
                watchdog = threading.Thread(
                    target=self._watchdog_loop,
                    name="em-amqp-watchdog",
                    daemon=True,
                )
                watchdog.start()
                self._threads.append(watchdog)
        logger.info(
            "Event Mesh AMQP consumer started (url=%s, queues=%d, concurrency=%d)",
            self._url(), len(self._queues), self._concurrency,
        )

    def stop(self, join_timeout_s: float = 10.0) -> None:
        with self._lock:
            self._running = False
            threads = list(self._threads)
            self._threads = []
        for t in threads:
            t.join(timeout=join_timeout_s)
        logger.info("Event Mesh AMQP consumer stopped")

    # ------------------------------------------------------------------
    # Liveness watchdog
    # ------------------------------------------------------------------

    def check_liveness(self, now: Optional[float] = None) -> list[str]:
        """Stand down any link that is attached but not delivering.

        The failure this exists for: on tenant-1, six receiver links opened
        cleanly and then delivered nothing — no errors, no detaches, no
        disconnects. ``_receive_loop`` only reconnects when ``run()`` returns,
        and a silent link never returns, so the loop spun forever consuming
        nothing while runs hung with every node NOT_STARTED.

        A stall needs BOTH halves: the link has delivered nothing since the
        last check AND the broker says the queue has messages waiting. Depth
        alone is not a stall (the handler may simply be slow), and a quiet
        queue is not a stall either — that is just an idle system.

        Returns the queues stood down, for tests and metrics.
        """
        now = time.time() if now is None else now
        if self._queue_depth is None:
            return []
        with self._lock:
            snapshot = list(self._bridges.items())
        stood_down: list[str] = []
        attached = 0
        # One depth read per QUEUE per pass, even when several links share it —
        # otherwise raising concurrency multiplies management-API calls.
        depths: dict[str, Optional[int]] = {}
        for key, bridge in snapshot:
            # Tolerate a bare queue-name key: `key[0]` on a str yields its first
            # CHARACTER, which would quietly label every metric "s" instead of
            # failing. A silent wrong answer is the worst outcome here.
            queue_name = key[0] if isinstance(key, tuple) else key
            if not bridge.ready:
                # Still handshaking or already down — the reconnect loop owns it.
                self._liveness.pop(key, None)
                continue
            attached += 1
            state = self._liveness.setdefault(key, [0, None, 0, bridge.epoch])
            if state[3] != bridge.epoch:
                # A RECONNECT happened on this link. ``deliveries`` restarts at
                # 0 on the new bridge while ``state[0]`` still holds the old
                # bridge's total, so every comparison below would read "no
                # progress" until the new link had re-delivered the old link's
                # entire lifetime count. On a busy queue that is effectively
                # never: the watchdog would stand down a healthy, actively
                # delivering link, its replacement would start from 0 again,
                # and the delivery counter would flatline — manufacturing the
                # exact symptoms this watchdog exists to detect. A 12-hour
                # token rotation was enough to trigger it; no concurrency
                # needed.
                #
                # Reset the baseline and the stall timer; KEEP the forced
                # streak, because a queue that genuinely cannot deliver must
                # still widen its window rather than reconnect-storm.
                state[0], state[1], state[3] = 0, None, bridge.epoch
            delivered = bridge.deliveries
            if delivered > state[0]:  # progress — nothing to see
                self._emit("record_amqp_delivery", queue_name, delivered - state[0])
                state[0], state[1], state[2] = delivered, None, 0
                continue
            if queue_name not in depths:
                depths[queue_name] = self._safe_depth(queue_name)
            depth = depths[queue_name]
            if not depth:  # 0, None (unreadable) or unknown → not a stall
                state[1] = None
                continue
            if state[1] is None:
                state[1] = now
                continue
            # Widen the window after repeated forced reconnects so a queue that
            # genuinely cannot deliver doesn't reconnect-storm the broker.
            threshold = _STALL_AFTER_S * min(2 ** state[2], _STALL_BACKOFF_MAX)
            if now - state[1] < threshold:
                continue
            logger.error(
                "AMQP link is attached but silent (queue=%s): %d message(s) "
                "waiting, 0 delivered for %.0fs — standing the link down to "
                "force a reconnect (forced %d time(s) so far)",
                queue_name, depth, now - state[1], state[2] + 1,
            )
            bridge.request_stop("watchdog_stall")
            self.forced_reconnects += 1
            state[1], state[2] = None, state[2] + 1
            stood_down.append(queue_name)
            self._emit("record_amqp_stall", queue_name)
        # Gauges last, so "stalled" reflects the decisions just made.
        stalled = sum(1 for s in self._liveness.values() if s[1] is not None)
        self._emit("record_amqp_links", attached, stalled + len(stood_down))
        return stood_down

    def _emit(self, method: str, *args: Any) -> None:
        """Report a metric, never letting the reporting break consumption."""
        if self._metrics is None:
            return
        try:
            getattr(self._metrics, method)(*args)
        except Exception:
            logger.debug("AMQP metric %s failed", method, exc_info=True)

    def _safe_depth(self, queue_name: str) -> Optional[int]:
        try:
            depth = self._queue_depth(queue_name)  # type: ignore[misc]
        except Exception:
            logger.warning("AMQP watchdog could not read depth (queue=%s)", queue_name)
            return None
        return depth if isinstance(depth, int) else None

    def _watchdog_loop(self) -> None:
        while self._running:
            self._sleep_interruptible(_STALL_CHECK_INTERVAL_S)
            if not self._running:
                break
            try:
                self.check_liveness()
            except Exception:
                # The watchdog must never be the thing that kills consumption.
                logger.exception("AMQP liveness check failed")
        logger.info("AMQP watchdog stopped")

    def _spawn_queue_threads(self, queue_name: str) -> None:
        for i in range(self._concurrency):
            t = threading.Thread(
                target=self._receive_loop,
                args=(queue_name, i),
                name=f"em-amqp-recv-{queue_name}-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)

    # ------------------------------------------------------------------
    # Connect probe (used by the broker for auto-fallback to REST)
    # ------------------------------------------------------------------

    def probe(self, queue_name: str) -> bool:
        if not amqp_available():
            logger.warning(
                "AMQP probe skipped — proton/websocket-client not importable: %s",
                _AMQP_IMPORT_ERROR,
            )
            return False
        bridge = _ProtonWsBridge(
            url=self._url(),
            address=self._source_address(queue_name),
            queue_name=queue_name,
            token=self._token(),
            # NOT self._prefetch: the probe stops pumping the instant the link
            # is ready, so any credit it grants buys messages it will never
            # settle. See _close's note on stranding.
            prefetch=_PROBE_PREFETCH,
            message_handler=self._message_handler,
            is_running=lambda: True,
            hostname=self._host,
        )
        bridge.run(deadline=time.monotonic() + self._probe_timeout_s)
        if bridge.ready:
            logger.info("Event Mesh AMQP probe OK (url=%s, queue=%s)", self._url(), queue_name)
            return True
        # Rich diagnostics so a silent stall is debuggable from the log:
        #   events=[] recv=0          → WS upgraded but no AMQP reply (subproto?)
        #   sasl=1                    → SASL auth rejected (token/username)
        #   sasl=0 + CONNECTION/SESSION REMOTE_OPEN but no link → address/routing
        logger.warning(
            "Event Mesh AMQP probe failed (url=%s, queue=%s): %s | events=%s "
            "event_total=%d event_counts=%s sent=%dB recv=%dB sasl_outcome=%s",
            self._url(), queue_name,
            bridge.error or "no link opened before timeout",
            bridge.events_seen, bridge.events_seen.total,
            bridge.events_seen.counts(), bridge.bytes_sent, bridge.bytes_recv,
            bridge.sasl_outcome,
        )
        return False

    # ------------------------------------------------------------------
    # Receive loop
    # ------------------------------------------------------------------

    def _receive_loop(self, queue_name: str, index: int = 0) -> None:
        address = self._source_address(queue_name)
        key = (queue_name, index)
        backoff = _RECONNECT_BASE_S
        while self._running:
            token, expires_at = self._token_with_expiry()
            bridge = _ProtonWsBridge(
                url=self._url(),
                address=address,
                queue_name=queue_name,
                token=token,  # fresh token each (re)connect
                expires_at=expires_at,
                prefetch=self._prefetch,
                message_handler=self._message_handler,
                is_running=lambda: self._running,
                hostname=self._host,
                epoch=self._next_epoch(),
            )
            # Publish the live bridge so the watchdog can see its delivery
            # counter and stand it down; it is otherwise a local, which is why
            # nothing could detect a silent link. Keyed per LINK, so every one
            # of ``concurrency`` links on this queue is watched, not just the
            # last to register.
            with self._lock:
                self._bridges[key] = bridge
            try:
                bridge.run()  # blocks until disconnect/stop
            finally:
                with self._lock:
                    if self._bridges.get(key) is bridge:
                        self._bridges.pop(key, None)
            if not self._running:
                break
            # A deliberate stand-down (token rotation, watchdog) is not a
            # failure: reconnect immediately with a fresh token.
            if bridge.stood_down:
                self._emit("record_amqp_reconnect", queue_name, bridge.stood_down)
                backoff = _RECONNECT_BASE_S
                continue
            self._emit("record_amqp_reconnect", queue_name, "disconnect")
            # Reset the backoff only after a session that actually LASTED.
            # ``ready`` alone is not health: a link the broker attaches and
            # then immediately rejects sets it, and resetting on that spins
            # the reconnect loop at full speed forever.
            session_s = (
                time.monotonic() - bridge.attached_at if bridge.attached_at else 0.0
            )
            if bridge.ready and session_s >= _HEALTHY_SESSION_S:
                backoff = _RECONNECT_BASE_S
            else:
                logger.warning(
                    "AMQP receiver (queue=%s) ended after %.1fs (attached=%s): %s "
                    "— retry in %.1fs",
                    queue_name, session_s, bool(bridge.attached_at),
                    bridge.error, backoff,
                )
                self._sleep_interruptible(backoff)
                backoff = min(_RECONNECT_MAX_S, backoff * 2)
        logger.info("AMQP receiver loop exited (queue=%s)", queue_name)

    def _sleep_interruptible(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))

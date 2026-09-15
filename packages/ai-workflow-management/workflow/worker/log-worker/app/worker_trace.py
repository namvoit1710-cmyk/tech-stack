"""Shared local trace — proves WHICH worker process handled a node.

The deployed/server worker runs older code without this, so a line here == a LOCAL
worker was invoked (flow done but no line here => the task went to the server).

Each line carries a per-process IDENTITY (host + ip + pid + instance) computed once at
import, so MULTIPLE local workers — same machine (distinct pid/instance) or different
machines (distinct host/ip) — are told apart in one shared file. Never logs a
secret/token. Trace file is ``$LOG_WORKER_TRACE_FILE`` or ``~/log-worker-local-trace.log``.
"""

from __future__ import annotations

import datetime
import os
import socket
import uuid

_TRACE_FILE = os.environ.get(
    "LOG_WORKER_TRACE_FILE",
    os.path.join(os.path.expanduser("~"), "log-worker-local-trace.log"),
)


def _primary_ip() -> str:
    """The machine's primary outbound IP (not 127.0.0.1). Best-effort; '?' on failure.

    ``gethostbyname(gethostname())`` often returns 127.0.0.1; the UDP-connect trick asks
    the OS which local interface would route outbound, without sending a packet."""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return "?"
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass


# Computed ONCE per process → a stable identity for this worker instance.
_HOST = socket.gethostname()
_PID = os.getpid()
_INSTANCE = uuid.uuid4().hex[:6]
_IP = _primary_ip()
IDENTITY = f"host={_HOST} ip={_IP} pid={_PID} inst={_INSTANCE}"


def trace(msg: str) -> None:
    """Append one identified trace line. Never raises (tracing must not break a node)."""
    try:
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with open(_TRACE_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  [LOCAL {IDENTITY}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass

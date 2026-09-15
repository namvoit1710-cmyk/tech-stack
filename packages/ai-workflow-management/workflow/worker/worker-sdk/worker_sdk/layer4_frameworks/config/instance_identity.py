"""Process identity for worker registration (SA-2055 / R8).

The SDK answers exactly ONE question here: *which process am I?* It does NOT
invent, derive or persist a registration id — the executor owns that
(``id = f"{instance_id}:{worker_type}"``, keystone §4), so the boundary owns
uniqueness and no worker can collide with another worker's key.

Why the INDEX and never the GUID: Diego keys an ActualLRP by
``ActualLRPKey{ProcessGuid, Index, Domain}``; a crash goes
CRASHED -> UNCLAIMED (empty instance key) -> CLAIMED/RUNNING (new instance key),
so the ``ActualLRPInstanceKey{InstanceGuid, CellId}`` is cleared and reissued
while the Index is not. ``CF_INSTANCE_GUID`` would mint one orphan registry row
per restart, unbounded; ``CF_INSTANCE_INDEX`` makes a restarted replica re-use
its own rows (keystone §6).

Off CF (local dev, tests) the env vars are absent -> blank -> the executor
falls back to uuid4-per-register-call, which is already correct for counting
(keystone §4.2). Fails safe: never worse than today.
"""

import json
import os
import socket
from datetime import datetime, timezone

# Computed ONCE, at import — NOT per register call. jira-worker registers 22
# node types from ONE process; a per-call timestamp would make one process look
# like 22 processes that started at 22 different moments (spec §5).
STARTED_AT: str = datetime.now(timezone.utc).isoformat()


def instance_id() -> str:
    """``f"{application_name}_{CF_INSTANCE_INDEX}"`` — e.g. ``jira-worker_0``.

    Blank when either half is missing or unusable (not on CF, or a malformed
    ``VCAP_APPLICATION``). See the module docstring for why blank is safe.

    NEVER reads the ``APP_NAME`` env var. All 10 workers set it, but it is a
    human DISPLAY name — a spaced string like ``"HTTP Request Worker"``. It
    has spaces and is not unique-by-construction. The CF app name lives in
    ``VCAP_APPLICATION``.
    """
    index = os.environ.get("CF_INSTANCE_INDEX", "").strip()
    if not index:
        return ""
    raw = os.environ.get("VCAP_APPLICATION", "").strip()
    if not raw:
        return ""
    try:
        app_name = (json.loads(raw) or {}).get("application_name", "")
    except (ValueError, AttributeError):
        # ValueError: not JSON. AttributeError: valid JSON but not an object
        # (a list/str has no .get). Either way -> blank -> uuid4 path.
        return ""
    if not app_name:
        return ""
    return f"{app_name}_{index}"


def worker_app() -> str:
    """The CF ``application_name`` — the deployable this process runs as, e.g.
    ``"jira-worker"``.

    Unlike instance_id(), this needs NO ``CF_INSTANCE_INDEX``: the app name is
    identical across every replica of a deployable, which is precisely the
    point — it is a grouping key across instances, not a per-instance one.
    Requiring the index here would blank this out on every replica but the
    one CF happens to route CF_INSTANCE_INDEX-bearing requests to, which is
    not a restriction this value needs.

    Blank off CF (local dev, tests) or on a malformed ``VCAP_APPLICATION`` —
    fails safe, same contract as instance_id(). The registration executor
    (R14) falls back to deriving a value from the endpoint host when this is
    blank, so a blank here is a degraded-but-working state, never a crash.

    NEVER the ``APP_NAME`` env var, for the same reason instance_id() never
    reads it: it is a human DISPLAY name — a spaced string like
    ``"HTTP Request Worker"`` — not unique by construction. The CF app name
    lives only in ``VCAP_APPLICATION``.
    """
    raw = os.environ.get("VCAP_APPLICATION", "").strip()
    if not raw:
        return ""
    try:
        return (json.loads(raw) or {}).get("application_name", "") or ""
    except (ValueError, AttributeError):
        # ValueError: not JSON. AttributeError: valid JSON but not an object
        # (a list/str has no .get). Either way -> blank -> executor fallback.
        return ""


def host() -> str:
    """The container IP on CF, else the hostname. Diagnostic only — never a key."""
    return os.environ.get("CF_INSTANCE_IP", "").strip() or socket.gethostname()


def pid() -> int:
    """OS pid. Near-constant on CF (1 container = 1 process); it earns its place
    by disambiguating two worker processes sharing a host in local dev."""
    return os.getpid()

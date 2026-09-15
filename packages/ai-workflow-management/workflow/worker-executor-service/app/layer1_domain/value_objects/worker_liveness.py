"""Worker liveness vocabulary: what counts as alive, and how a heartbeat
timestamp is read.

Both lived inside MemoryWorkerRegistryStore (layer4) until SA-2053 (R6). They
move here because layer2 use cases need them and **no layer2 module in this
service imports layer4** — copying them up would fork the definition of "alive"
and add a THIRD tolerant parser to this repo (the control plane already carries
its own copy from SA-2052, in a separate deployable that cannot be imported from
here: both services are packaged as `app`).
"""
from datetime import datetime, timezone
from typing import Optional

# The statuses that count as alive. Mirrors the control-plane palette's filter
# (worker_node_provider `_HEALTHY_STATUSES`) EXACTLY, blank/None included: a node
# the palette lists as available must also resolve here, or a user drags in a
# node the executor then refuses to dispatch to (a split brain). Blank/None arise
# only at boundaries -- a gRPC heartbeat that omits status (proto3 string defaults
# to "") or a NULL health_status column rehydrated by the HANA store -- where
# "assume alive" matches the palette and is safer than "silently dead". The entity
# default is "healthy", so no normally-constructed row is blank. "degraded" is
# excluded here AND by the palette (coherent); whether a degraded worker should
# still resolve is SA-2051's call.
#
# SA-2053 NOTE: R6's `oldest_heartbeat_age_seconds` deliberately uses a NARROWER
# population than this tuple (only "healthy"/"active", and only within the stale
# window). That narrowing lives in the use case as its own named constant. Do NOT
# narrow this one to match: it is shared with the palette, dispatch and the
# sweeper, and moving it moves all three at once.
HEALTHY_STATUSES = (None, "", "healthy", "active")

# Blank/unparseable sorts oldest -- safe to ORDER by, never safe to SUBTRACT.
_UNKNOWN_TS = datetime.min.replace(tzinfo=timezone.utc)


def parse_ts(raw: str) -> datetime:
    """Tolerant ISO-8601 parse for ``last_heartbeat``.

    ``last_heartbeat`` is a str with several producers: the entity default
    (``...+00:00``), a HANA row (driver-formatted, often space-separated and
    tz-naive), and blank for a registration that has never heartbeated. Comparing
    those as strings picks the wrong instance, so freshest-healthy selection goes
    through here. Blank or unparseable sorts oldest -- it must never win.

    Callers that SUBTRACT this value must first check for the blank/unparseable
    sentinel (``datetime.min`` UTC): ``now - datetime.min`` is ~6.4e10 seconds and
    would render as "last seen 2,000 years ago". Ordering is safe; arithmetic is not.

    NOT a store-wide guarantee: the registry-cap eviction path in ``register``
    (the ``cutoff`` comparison and ``min(stale, key=...)``) compares
    ``last_heartbeat`` as raw strings and never calls this function. A
    HANA-format value (``"2026-07-15 12:59:30"``) would string-sort below an
    ISO cutoff (space ``0x20`` < ``T`` ``0x54`` at index 10) and be judged stale
    while actually fresh. Not reachable today -- every ``last_heartbeat`` that
    store writes or receives is ``.isoformat()`` -- but this function does not
    protect that path; ledger it rather than assuming coverage.
    """
    if not raw:
        return _UNKNOWN_TS
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return _UNKNOWN_TS
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_seconds(raw: str, now: datetime) -> Optional[float]:
    """Seconds since ``raw``, or None when it cannot be measured.

    Lives beside ``parse_ts`` because the ``datetime.min`` sentinel is parse_ts's
    OWN contract -- the function that produces it owns the rule for consuming it.
    Every caller that subtracts a parsed heartbeat needs this exact guard, so it is
    defined once rather than re-derived per feature (SA-2053 operator ruling).

    None means UNKNOWN. A number always means measured.
    """
    parsed = parse_ts(raw)
    if parsed == _UNKNOWN_TS:
        return None
    return (now - parsed).total_seconds()


def oldest_fresh_age_seconds(rows, stale_after_seconds, now=None):
    """Oldest heartbeat age (seconds) among the MEASURABLY-LIVE replicas in ``rows``.

    Population = health in ``HEALTHY_STATUSES`` (broad) AND age is measurable
    (heartbeat parseable — ``age_seconds`` returns a number) AND age is within the
    stale window (``age <= stale_after_seconds``). Blank/unparseable heartbeats and
    stale/unhealthy rows are EXCLUDED. Returns ``None`` when the population is empty
    (no measurably-live replica) — never ``0`` (``0`` would read as "just
    heartbeated"). R6 (SA-2053) reuses this for its oldest_heartbeat_age_seconds
    field — one definition, no divergence.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    ages = []
    for r in rows:
        if getattr(r, "health_status", None) not in HEALTHY_STATUSES:
            continue
        age = age_seconds(getattr(r, "last_heartbeat", "") or "", now)
        if age is None or age > stale_after_seconds:
            continue
        ages.append(age)
    return max(ages) if ages else None


def replica_gauge_stats(rows, stale_after_seconds, now=None):
    """Group registry rows into the R7 gauge snapshot.

    Returns::

        {"counts": {(worker_type, delivery_mode): {"total": int, "healthy": int}},
         "age":    {worker_type: float | None}}

    ``counts`` is per (worker_type, delivery_mode): ``total`` counts rows,
    ``healthy`` counts rows whose status is in ``HEALTHY_STATUSES`` (broad — parity
    with ``count_healthy``/``replica_summary``). Rows are one-per-(instance_id,
    worker_type) in the store, so counting rows within a (type, mode) equals
    counting distinct instances. ``age`` is per worker_type via
    ``oldest_fresh_age_seconds`` (narrow population). Rows with empty worker_type
    are skipped. ``delivery_mode`` is read tolerantly: a ``DeliveryMode`` enum
    yields its ``.value`` ("push"/"pull"); a plain string is used as-is.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    counts: dict = {}
    by_type: dict = {}
    for r in rows:
        wt = getattr(r, "worker_type", "") or ""
        if not wt:
            continue
        mode = getattr(r, "delivery_mode", "")
        mode = getattr(mode, "value", mode) or ""
        entry = counts.setdefault((wt, mode), {"total": 0, "healthy": 0})
        entry["total"] += 1
        if getattr(r, "health_status", None) in HEALTHY_STATUSES:
            entry["healthy"] += 1
        by_type.setdefault(wt, []).append(r)
    age = {
        wt: oldest_fresh_age_seconds(type_rows, stale_after_seconds, now)
        for wt, type_rows in by_type.items()
    }
    return {"counts": counts, "age": age}

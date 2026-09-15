"""SA-2047 — aggregation rules shared by every replica read.

Lifted out of list_worker_replicas_usecase (R6) when R14 added a second
aggregate over the same registrations. Two copies of a liveness rule drifting
apart is the exact failure this epic exists to fix, so there is one copy and
both aggregates import it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer1_domain.value_objects.worker_liveness import HEALTHY_STATUSES, parse_ts, age_seconds


def representative_mode(instances: list[WorkerRegistration]) -> str:
    """The type's delivery_mode: freshest healthy -> freshest of all -> "".

    Total by construction: no exception path, no [0]. `[0]` would be arbitrary but
    STABLE -- deterministic in tests, drifting in production -- which is the worst
    failure mode available here.

    The empty case returns "" and NOT normalize_delivery_mode("") -- that helper maps
    blank to PUSH as SA-2026's INGEST back-compat rule for old workers that send ""
    on the wire. On an OUTPUT path it silently fabricates "push" for something we
    could not determine, indistinguishable from a genuine push worker.

    `.value`, never str(): DeliveryMode subclasses str (so == "push" works) but
    Enum.__str__ still wins, and str() emits "DeliveryMode.PUSH".
    """
    healthy = [w for w in instances if w.health_status in HEALTHY_STATUSES]
    pool = healthy or instances
    if not pool:
        return ""
    return max(pool, key=lambda w: parse_ts(w.last_heartbeat)).delivery_mode.value


# Q13 (ruled 2026-07-21). DELIBERATELY NARROWER than HEALTHY_STATUSES: blank and
# None are excluded here. `replicas_healthy` counts them (via the store's
# count_healthy) and this metric does not -- an instance with no measurement cannot
# be the worst measurement. The two numbers disagree on purpose; the DTO says so.
# Do NOT "fix" the divergence by narrowing HEALTHY_STATUSES: that tuple is shared
# with the palette, dispatch and the sweeper.
LIVE_STATUSES = ("healthy", "active")


def oldest_live_age(instances: list[WorkerRegistration], stale_after_seconds: int,
                    now: datetime) -> Optional[float]:
    """Worst staleness among instances that are BOTH explicitly healthy AND inside
    the stale window. None when nobody qualifies -- UNKNOWN, never 0 (0 reads as
    "every replica just heartbeated", the opposite of the truth).

    `age_seconds` (layer1) already returns None for a blank or unparseable
    heartbeat, so an unmeasurable instance drops out here without this function
    re-deriving the sentinel rule.
    """
    ages: list[float] = []
    for w in instances:
        if w.health_status not in LIVE_STATUSES:
            continue
        age = age_seconds(w.last_heartbeat, now)
        if age is None:                    # unmeasurable -> cannot be the worst measurement
            continue
        if age <= stale_after_seconds:
            ages.append(age)
    return max(ages) if ages else None

"""Worker health realtime feed (SA-2056 R9; rekeyed to worker_app by SA-2047
R14 Task 15).

One ``execute()`` = one differ tick, driven by the CP background loop (main.py
lifespan, opt-in WORKER_HEALTH_FEED_ENABLED). Reads R14's /workers/apps via the
registry client (Task 13's ``get_app_summary``), diffs against the last
published snapshot, and publishes one ``worker.app.health`` envelope per
changed worker_app onto realtime.events — room ``workers:health`` (unchanged
by the rekey), ``_run_key``/``_run_seq`` unset so the push gateway forwards it
without per-run resequencing.

Correctness rails (spec §4.1/§6; all pre-existing, all still enforced after
the rekey — the rows changed shape, the rails did not):
  * unreachable (None) != empty ([]): a transport flap keeps the snapshot and
    publishes nothing; a genuine empty 200 tombstones every last-known app.
    ``get_app_summary`` deliberately folds a non-list body into None too
    (see its docstring) for exactly this reason — a malformed response must
    never masquerade as "the fleet is empty";
  * cold-start seeds silently (R10 gets full state from R6's snapshot-on-connect);
  * no broker -> no-op (never crash the loop);
  * ``oldest_heartbeat_age_seconds`` drifts every tick and is carried in the
    published payload ONLY, never folded into the change-detection tuple
    below — else the feed would publish on literally every tick, forever.

New in this rekey: ``replica_identity`` ("pending"/"known", Task 13) joins the
change-detection tuple. A pending -> known transition is a state change the
page must see even on a tick where the counts underneath don't move.

Published directly to the broker, NOT via RealtimeBroadcastingPublisher, whose
publish() drops roomless events (broadcasting_publisher.py:195-196).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

REALTIME_TOPIC = "realtime.events"
HEALTH_EVENT = "worker.app.health"
HEALTH_ROOM = "workers:health"


class WorkerHealthFeedUseCase:
    def __init__(self, worker_registry_client: Any = None, logger: Any = None, **kwargs: Any) -> None:
        self._registry = worker_registry_client
        self.logger = logger
        # Cross-wired in build_app_container (the broker is not in
        # available_dependencies), exactly like R3's _repo.
        self._broker: Any = None
        # worker_app -> (replicas_total, replicas_healthy, nodes_total,
        # nodes_healthy, delivery_mode, replica_identity, node_types) as of the
        # last published tick. replica_identity is IN this tuple deliberately
        # (module docstring) so a pending->known flip publishes even when the
        # counts don't move; oldest_heartbeat_age_seconds is deliberately NOT
        # (it would force a publish on every tick).
        #
        # node_types is a tuple, not the list off the wire: this whole thing is
        # compared by value every tick, and it also catches the one catalogue
        # change nodes_total cannot see — a same-size swap (one node type
        # retired as another appears leaves the count untouched).
        self._last: dict[str, tuple[int, int, int, int, str, str, tuple[str, ...]]] = {}
        self._seeded: bool = False

    async def execute(self, now: Optional[datetime] = None) -> dict:
        if self._broker is None or self._registry is None:
            return {"published": 0, "reason": "no_broker_or_registry"}
        now = now or datetime.now(timezone.utc)

        rows = self._registry.get_app_summary()
        # Unreachable (None) -> keep the snapshot, publish nothing. NOT `if not rows`:
        # a genuine [] must fall through and tombstone (§4.1).
        if rows is None:
            return {"published": 0, "reason": "unreachable"}

        current: dict[str, tuple[int, int, int, int, str, str, tuple[str, ...]]] = {
            r.get("worker_app", ""): (
                int(r.get("replicas_total", 0)),
                int(r.get("replicas_healthy", 0)),
                int(r.get("nodes_total", 0)),
                int(r.get("nodes_healthy", 0)),
                r.get("delivery_mode", "") or "",
                r.get("replica_identity", "") or "",
                tuple(r.get("node_types") or ()),
            )
            for r in rows if r.get("worker_app")
        }

        # Oldest-heartbeat age per app — carried in the published payload ONLY,
        # NOT in the change-detection tuple above (age drifts every tick, which
        # would force a publish every tick). Tombstones carry None.
        ages: dict[str, Any] = {
            r.get("worker_app", ""): r.get("oldest_heartbeat_age_seconds")
            for r in rows if r.get("worker_app")
        }

        # Cold-start: seed the baseline silently (a bare `if not self._last` cannot
        # tell "never ran" from "ran, registry now genuinely empty").
        if not self._seeded:
            self._last = current
            self._seeded = True
            return {"published": 0, "reason": "seeded"}

        # Counts publish *attempts*, not confirmed deliveries: `_publish()`
        # swallows a broker exception (see its comment below) and this still
        # increments, so `published` can overcount relative to what actually
        # reached the broker.
        published = 0
        at = now.isoformat()

        # Changed / new apps.
        for app, tup in current.items():
            if self._last.get(app) != tup:
                self._publish(app, tup, at, ages.get(app))
                published += 1
        # Tombstones: present last tick, absent now. Counts zero out and the
        # node-type list empties with them; mode (index 4) and replica_identity
        # (index 5) are carried forward from the last-known tuple so a tombstone
        # still says what the app WAS, not just that it's gone.
        for app, tup in self._last.items():
            if app not in current:
                self._publish(app, (0, 0, 0, 0, tup[4], tup[5], ()), at, None)
                published += 1

        self._last = current
        return {"published": published}

    def _publish(self, worker_app: str,
                 tup: tuple[int, int, int, int, str, str, tuple[str, ...]], at: str,
                 oldest_age: Optional[float] = None) -> None:
        replicas_total, replicas_healthy, nodes_total, nodes_healthy, mode, identity, node_types = tup
        envelope = {
            "event_type": HEALTH_EVENT,
            "rooms": [HEALTH_ROOM],
            "payload": {
                # Self-describing payload: the http push-gateway client forwards
                # ONLY the payload (drops the envelope event_type), and the FE
                # keys realtime events on payload._event (see getSocketEventName).
                # Mirrors broadcasting_publisher._serialize_event's payload["_event"].
                "_event": HEALTH_EVENT,
                "worker_app": worker_app,
                "delivery_mode": mode,
                "nodes_total": nodes_total,
                "nodes_healthy": nodes_healthy,
                # Carried so a realtime patch never strips the node-type list
                # off a row the snapshot fetch already populated — the FE
                # replaces the row wholesale (patchAppAggregate), it does not
                # merge field by field.
                "node_types": list(node_types),
                "replicas_total": replicas_total,
                "replicas_healthy": replicas_healthy,
                "replica_identity": identity,
                "oldest_heartbeat_age_seconds": oldest_age,
                "at": at,
            },
            "_run_key": None,      # unset -> push gateway forwards without resequencing
            "_run_seq": None,
        }
        try:
            self._broker.publish_to_topic(REALTIME_TOPIC, envelope)
        except Exception as exc:
            # Broker hiccup: log and drop this delta. execute() advances
            # self._last to `current` unconditionally right after this loop
            # (see :81 area), regardless of whether this publish succeeded --
            # so a failed publish is NOT retried next tick. The dashboard
            # stays stale for this worker_app until the NEXT real count
            # change produces a fresh delta to diff against; there is no
            # other self-heal path.
            self._log("error", "Worker health feed publish failed", error=str(exc))

    def _log(self, level: str, msg: str, **kw: Any) -> None:
        if self.logger is not None:
            getattr(self.logger, level, lambda *a, **k: None)(msg, **kw) if kw else \
                getattr(self.logger, level, lambda *a, **k: None)(msg)

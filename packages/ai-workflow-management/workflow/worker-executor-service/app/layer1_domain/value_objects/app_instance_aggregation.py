"""SA-2047 R14 (Task 6) -- the app/instance counting rule, shared by every store.

``IWorkerRegistryStore.app_summary`` and ``.list_app_instances`` answer "how many
WORKERS do we have" the same way ``replica_summary``/``list_instances`` answer
"how many REPLICAS of this type" -- and for the same reason ``replica_aggregation.py``
exists: two copies of a counting rule is the exact drift SA-2047 was opened to
fix. Tasks 4-5 wrote this rule once, inline, inside ``MemoryWorkerRegistryStore``.
Task 6 gave ``HanaWorkerRegistryStore`` the same two reads; duplicating the fold
into a second copy would be a reviewable defect in its own right, so it moved
here instead, and the memory store now calls it too.

Both functions take a plain ``Iterable[WorkerRegistration]`` -- whatever the
caller's OWN row-loading already produced (``self._store.values()`` for the
memory store, ``[self._row_to_entity(row) for row in rows]`` for the HANA store)
-- and know nothing about where those rows came from. That is deliberate: the
counting rule lives here exactly once, and every store is a thin loader in
front of it. See ``worker_registration.py`` for the three-identity vocabulary
(``worker_type`` = node type / class, ``instance_id`` = process, ``worker_app``
= the deployable that process runs as) this module counts over.
"""
from __future__ import annotations

from typing import Iterable

from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer1_domain.value_objects.worker_liveness import HEALTHY_STATUSES, parse_ts


def app_summary(registrations: Iterable[WorkerRegistration]) -> dict[str, dict]:
    """``{worker_app: {nodes_total, nodes_healthy, node_types, replicas_total,
    replicas_healthy, all_synthesized}}`` -- the read behind /workers/apps.

    Two different units in one row, deliberately: nodes are NODE TYPES
    (jira-worker offers 22) and replicas are PROCESSES (it runs 1). Counting
    both as "rows" is the defect R14 exists to fix.

    Nodes are counted as DISTINCT ``worker_type``, not as registrations. A
    process registers one row per node type it serves, so registrations are
    ``node types x replicas``: counting rows made jira-worker read "44 nodes"
    the moment it scaled to 2 instances, and a 1-node app on 2 replicas read
    "2 nodes". Both were witnessed live (see the R14 real-worker evidence note)
    and both are the same conflation this epic set out to remove -- a node type
    is catalogue, and scaling a deployable does not add node types to it.
    ``node_types`` carries the names themselves, sorted, so the caller can list
    them without a second round trip.

    A synthesized instance_id is excluded from the replica sets entirely: it is
    a per-registration uuid4, so counting it would report one process as 22
    replicas. ``all_synthesized`` starts True per app and flips to False the
    moment any non-synthesized row appears; it never flips back. When an app has
    nothing but synthesized ids, the replica counts are 0 and ``all_synthesized``
    says why -- the caller renders "pending", never a bare 0.
    """
    acc: dict[str, dict] = {}
    for w in registrations:
        row = acc.setdefault(w.worker_app, {
            "node_types": set(), "healthy_node_types": set(),
            "replica_ids": set(), "healthy_replica_ids": set(),
            "all_synthesized": True,
        })
        row["node_types"].add(w.worker_type)
        healthy = w.health_status in HEALTHY_STATUSES
        if healthy:
            # A node type is healthy when at least ONE replica can serve it.
            # Requiring every replica would report a type as down while a live
            # process is still answering for it.
            row["healthy_node_types"].add(w.worker_type)
        if w.instance_synthesized:
            continue
        row["all_synthesized"] = False
        row["replica_ids"].add(w.instance_id)
        if healthy:
            row["healthy_replica_ids"].add(w.instance_id)
    return {
        app: {
            "nodes_total": len(r["node_types"]),
            "nodes_healthy": len(r["healthy_node_types"]),
            "node_types": sorted(r["node_types"]),
            "replicas_total": len(r["replica_ids"]),
            "replicas_healthy": len(r["healthy_replica_ids"]),
            "all_synthesized": r["all_synthesized"],
        }
        for app, r in acc.items()
    }


def list_app_instances(
    registrations: Iterable[WorkerRegistration], worker_app: str,
) -> list[tuple[WorkerRegistration, list[str]]]:
    """One entry per PROCESS of an app: (freshest registration, node types).

    ``list_instances`` answers the analogous question for a worker_type, where a
    process owns exactly one row. Here it owns one row PER node type it serves,
    so the rows are folded by ``instance_id`` and the freshest heartbeat --
    compared via ``parse_ts``, never a raw string compare -- represents the
    process. Same freshest-wins rule ``get_by_worker_type`` uses, and for the
    same reason: the other rows carry identical process facts. A blank or
    unknown ``worker_app`` returns ``[]`` rather than folding the whole registry
    under an unnamed bucket.
    """
    if not worker_app:
        return []
    folded: dict[str, tuple[WorkerRegistration, list[str]]] = {}
    for w in registrations:
        if w.worker_app != worker_app:
            continue
        current = folded.get(w.instance_id)
        if current is None:
            folded[w.instance_id] = (w, [w.worker_type])
            continue
        reg, nodes = current
        nodes.append(w.worker_type)
        if parse_ts(w.last_heartbeat) > parse_ts(reg.last_heartbeat):
            folded[w.instance_id] = (w, nodes)
    return list(folded.values())

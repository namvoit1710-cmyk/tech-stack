#!/usr/bin/env python3
"""Cleanup script: delete ORPHANED per-instance queues on SAP Event Mesh.

Every process running ``ResponseTopicListener`` provisions a queue named after
its identity (``sub-orchestration-responses-<sha256(instance_id)[:8]>``). A
graceful ``stop()`` releases it; a SIGKILL, OOM or crash does not, and the queue
is left behind with no listener that can ever re-adopt it. Enough of those and
the Event Mesh plan quota fills, ``create_queue`` returns 422, and new pods boot
``degraded`` — dropping cross-instance responses.

Since SA-2087 the identity derives from ``<application_name>_<CF_INSTANCE_INDEX>``
(see ``app/layer4_frameworks/config/instance_identity.py``), so a *restart*
re-adopts its own queue and produces no orphan at all. What still can:

* pods that ran before that fix (random uuid4 identity — one queue per boot);
* scaling **down** — indexes 1..N-1 keep their queues after a scale back to 1;
* renaming the app.

Usage
-----

    python3 tools/cleanup_event_mesh_queues.py --dry-run    # list, delete nothing
    python3 tools/cleanup_event_mesh_queues.py              # prompt per queue
    python3 tools/cleanup_event_mesh_queues.py --yes        # after a dry run

Run it where the app's env is wired — ``cf ssh`` into a deployed pod, from
``/app``, with ``PYTHONPATH=/app``.

Safety
------

A queue **name** is not proof that a queue is an orphan. Before deleting
anything, this checks per queue that it is:

* not this deployment's own live shard (derived the same way the app derives
  it, for indexes ``0..--max-index``);
* carrying **no consumers** — something is still attached otherwise;
* **empty** — a queue with messages is a backlog, not litter (``--force`` to
  override, deliberately separate from ``--yes``).

The previous version matched on name alone, and two of its three default
patterns would have deleted live queues: ``^sub-[\\w-]+\\.[\\w.-]+$`` matches
``sub-workflow.tasks`` (the executor's REST queue, and its rollback target),
and ``^sub-orchestration-responses-[0-9a-f]+$`` matches the LIVE response queue
whenever AMQP is off — it missed only because the live one currently carries an
``-amqp`` suffix. It also could not run at all: it read ``broker._management_url``
and ``broker._namespace``, both of which moved into ``EventMeshAdmin`` /
``EventMeshNames`` in the messaging refactor, so the "manual broom" the code
points operators at died with ``AttributeError`` before listing anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys

# Only the per-instance response queues. Deliberately NOT a general
# "sub-<anything containing a dot>" sweep: shared queues have names of that
# shape and are not orphans.
_DEFAULT_PATTERNS = [
    # Current format: sha256 shard, optionally transport-suffixed.
    r"^sub-orchestration-responses-[0-9a-f]{6,}(-amqp)?$",
    # Pre-hyphenation format, kept so old orphans stay reachable.
    r"^sub-orchestration\.responses\.[0-9a-f-]+$",
]

_logger = logging.getLogger(__name__)


def _strip_namespace(qualified: str, namespace: str) -> str:
    """Return the queue local-name (everything after the namespace prefix)."""
    if not namespace:
        return qualified
    prefix = namespace.rstrip("/") + "/"
    if qualified.startswith(prefix):
        return qualified[len(prefix):]
    return qualified


def _matches(local_name: str, regexes: list[re.Pattern]) -> bool:
    return any(rx.search(local_name) for rx in regexes)


def live_shards(max_index: int) -> set[str]:
    """Shards THIS deployment would use, for instance indexes ``0..max_index-1``.

    Derived exactly as the app derives it — ``sha256(f"{app}_{index}")[:8]`` over
    ``VCAP_APPLICATION.application_name`` — so the script cannot delete a queue a
    running replica is about to re-adopt. Empty off Cloud Foundry, where there is
    no deployment to protect.
    """
    raw = os.getenv("VCAP_APPLICATION", "").strip()
    if not raw:
        return set()
    try:
        vcap = json.loads(raw)
    except ValueError:
        return set()
    if not isinstance(vcap, dict):
        return set()
    app_name = vcap.get("application_name") or vcap.get("name") or ""
    if not isinstance(app_name, str) or not app_name.strip():
        return set()
    app_name = app_name.strip()
    return {
        hashlib.sha256(f"{app_name}_{i}".encode("utf-8")).hexdigest()[:8]
        for i in range(max(1, max_index))
    }


def namespace_from_vcap() -> str:
    """The broker namespace straight from the service binding.

    Needed because ``EVENT_MESH_NAMESPACE`` is often unset — the running app
    discovers its namespace lazily, by parsing it out of the broker's own 403,
    so a freshly-built client has an EMPTY one until it makes a request. With an
    empty namespace nothing is stripped, every local name still carries the
    ``<ns>/`` prefix, no pattern anchored at ``^sub-`` can match, and the tool
    prints "Nothing matched" — reporting *no orphans* when it simply could not
    see them. Measured on tenant-1: 49 queues listed, 0 matched.
    """
    raw = os.getenv("VCAP_SERVICES", "").strip()
    if not raw:
        return ""
    try:
        services = json.loads(raw)
    except ValueError:
        return ""
    if not isinstance(services, dict):
        return ""
    for bindings in services.values():
        if not isinstance(bindings, list):
            continue
        for binding in bindings:
            creds = (binding or {}).get("credentials") or {}
            ns = creds.get("namespace")
            if isinstance(ns, str) and ns.strip():
                return ns.strip()
    return ""


def _shard_of(local_name: str) -> str:
    """The shard segment of a response-queue local name, or ``''``."""
    m = re.search(r"([0-9a-f]{6,})(?:-amqp)?$", local_name)
    return m.group(1) if m else ""


def _build_broker():
    """Construct an EventMeshBrokerClient from env / VCAP_SERVICES."""
    # Deferred so --help works without the project being importable.
    from app.layer4_frameworks.config.app_config import settings
    from app.layer4_frameworks.messaging.event_mesh_broker_client import (
        EventMeshBrokerClient,
    )
    from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig

    return EventMeshBrokerClient(
        EventMeshConfig(
            token_url=settings.EVENT_MESH_TOKEN_URL,
            messaging_url=settings.EVENT_MESH_MESSAGING_URL,
            management_url=settings.EVENT_MESH_MANAGEMENT_URL,
            client_id=settings.EVENT_MESH_CLIENT_ID,
            client_secret=settings.EVENT_MESH_CLIENT_SECRET,
            namespace=settings.EVENT_MESH_NAMESPACE,
        )
    )


def classify(broker, qualified: str, protected: set[str], local: str,
             force: bool = False) -> tuple[bool, str]:
    """Return ``(deletable, reason)``. Only a PROVEN orphan is deletable."""
    shard = _shard_of(local)
    if shard and shard in protected:
        return False, f"live shard for this deployment ({shard})"
    body = broker.get_queue(qualified)
    if body is None:
        return False, "could not read the queue (unknown state)"
    consumers = body.get("consumerCount")
    depth = body.get("messageCount")
    if consumers:
        return False, f"{consumers} consumer(s) still bound"
    if depth and not force:
        return False, f"holds {depth} message(s) — use --force to delete anyway"
    return True, f"orphan (consumers=0, messages={depth or 0})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be deleted; delete nothing.")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Delete every proven orphan without prompting (use AFTER a dry run).")
    parser.add_argument("--force", action="store_true",
                        help="Also delete orphans that still hold messages. Separate from "
                             "--yes on purpose: skipping a prompt and destroying a backlog "
                             "are different decisions.")
    parser.add_argument("--pattern", action="append", dest="patterns",
                        help="Add a regex matching the LOCAL queue name. Repeatable.")
    parser.add_argument("--namespace", default=None,
                        help="Override the namespace used to strip local names.")
    parser.add_argument("--max-index", type=int, default=8,
                        help="Protect shards for instance indexes 0..N-1 (default 8). "
                             "Raise above your replica count before running.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")

    patterns = args.patterns or _DEFAULT_PATTERNS
    regexes = [re.compile(p) for p in patterns]
    print(f"Filtering with {len(regexes)} pattern(s):")
    for p in patterns:
        print(f"  {p}")

    broker = _build_broker()
    namespace = (args.namespace or getattr(broker, "namespace", "")
                 or os.environ.get("EVENT_MESH_NAMESPACE", "")
                 or namespace_from_vcap())
    if not namespace:
        print("\nERROR: could not resolve the broker namespace. Local queue names "
              "would keep their '<ns>/' prefix, nothing would match, and this "
              "script would report 'no orphans' without having looked. Pass "
              "--namespace explicitly.", file=sys.stderr)
        return 2
    protected = live_shards(args.max_index)
    print(f"\nNamespace: {namespace!r}")
    print(f"Protected live shards (indexes 0..{args.max_index - 1}): "
          f"{sorted(protected) if protected else 'none (not on Cloud Foundry)'}")

    queues = broker.list_queues()
    print(f"Listed {len(queues)} queue(s).\n")

    candidates = [(q, _strip_namespace(q, namespace)) for q in queues]
    candidates = [(q, local) for q, local in candidates if _matches(local, regexes)]
    if not candidates:
        print("Nothing matched the pattern(s).")
        return 0

    deletable: list[tuple[str, str, str]] = []
    kept: list[tuple[str, str, str]] = []
    for qualified, local in candidates:
        ok, reason = classify(broker, qualified, protected, local, args.force)
        (deletable if ok else kept).append((qualified, local, reason))

    if kept:
        print(f"{len(kept)} matched but KEPT:")
        for _q, local, reason in kept:
            print(f"  keep   {local:<52} {reason}")
    print(f"{len(deletable)} proven orphan(s):")
    for _q, local, reason in deletable:
        print(f"  delete {local:<52} {reason}")

    if not deletable:
        return 0
    if args.dry_run:
        print("\nDry run — nothing deleted.")
        return 0

    deleted = failed = 0
    for qualified, local, _reason in deletable:
        if not args.yes:
            try:
                resp = input(f"Delete {local}? [y/N] ").strip().lower()
            except EOFError:
                resp = ""
            if resp not in ("y", "yes"):
                print("  skipped")
                continue
        try:
            broker.delete_queue(qualified)
            print(f"  deleted {local}")
            deleted += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED to delete {local}: {exc}", file=sys.stderr)
            failed += 1

    print(f"\nDone. deleted={deleted} failed={failed} "
          f"skipped={len(deletable) - deleted - failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

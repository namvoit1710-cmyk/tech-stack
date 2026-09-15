"""Replay parked publishes from the ``publisher.dlq`` queue (SA-1471).

Terminally-failed publishes are parked on ``publisher.dlq`` as envelopes
``{original_kind, original_name, error, failed_at, payload_base64, ...}``.
After a broker incident is over, run this tool to drain the queue and
re-publish every envelope to its original topic/queue. Consumption uses
x-qos=1 (ack-after-republish), so an interrupted run redelivers instead
of losing messages; downstream consumers are idempotent per run/task keys.

Usage (same credential resolution as the app — env / VCAP_SERVICES):
    python tools/replay_publisher_dlq.py --dry-run          # peek only
    python tools/replay_publisher_dlq.py --max 500          # replay up to 500
    python tools/replay_publisher_dlq.py --yes              # replay everything
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import sys

logger = logging.getLogger("replay_publisher_dlq")

_DLQ_QUEUE = "publisher.dlq"


def _build_broker():
    """Construct an EventMeshBrokerClient from env / VCAP_SERVICES."""
    # Defer import so --help works without the project being importable.
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


def _decode(envelope: dict) -> tuple[str, str, dict] | None:
    """Return (kind, name, message) from a DLQ envelope, or None if malformed."""
    try:
        kind = envelope.get("original_kind", "topic")
        name = envelope["original_name"]
        message = json.loads(base64.b64decode(envelope["payload_base64"]))
        return kind, name, message
    except Exception:
        logger.exception("Malformed DLQ envelope — skipped: %.200s", str(envelope))
        return None


def replay_one(broker, envelope: dict, dry_run: bool) -> bool:
    """Re-publish one envelope to its original destination."""
    decoded = _decode(envelope)
    if decoded is None:
        return False
    kind, name, message = decoded
    if dry_run:
        print(f"  would replay {kind}={name} ({envelope.get('payload_size_bytes', '?')}B, "
              f"error was: {str(envelope.get('error', ''))[:80]})")
        return True
    if kind == "queue":
        broker.publish_to_queue(name, message)
    else:
        broker.publish_to_topic(name, message)
    print(f"  replayed {kind}={name}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Peek at envelopes without re-publishing or acking.")
    parser.add_argument("--max", type=int, default=1000,
                        help="Maximum envelopes to process this run (default 1000).")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip the confirmation prompt.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    broker = _build_broker()

    if not args.dry_run and not args.yes:
        answer = input(f"Replay up to {args.max} envelope(s) from {_DLQ_QUEUE}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    import asyncio

    async def _run() -> tuple[int, int]:
        replayed = skipped = 0
        if not args.dry_run:
            # publish_to_* enqueue into the async pump — it must be running,
            # and everything must share ONE event loop (hence asyncio.run).
            await broker.start_async()
        try:
            for _ in range(args.max):
                result = broker.consume_from_queue(_DLQ_QUEUE)
                if result is None:
                    break  # queue drained (or rate-limited — rerun later)
                envelope = result["data"]
                if isinstance(envelope, str):
                    try:
                        envelope = json.loads(envelope)
                    except json.JSONDecodeError:
                        envelope = {}
                ok = replay_one(broker, envelope, args.dry_run)
                if args.dry_run:
                    # Peek only: do NOT ack — lock TTL returns it to the queue.
                    skipped += 1
                    continue
                if ok:
                    replayed += 1
                msg_id = result.get("message_id")
                if msg_id:
                    broker.acknowledge(_DLQ_QUEUE, msg_id)
        finally:
            if not args.dry_run:
                # Drain the pump so every replayed publish hits the wire.
                await broker.stop_async(drain_timeout_s=30.0)
        return replayed, skipped

    try:
        replayed, skipped = asyncio.run(_run())
    finally:
        broker.close()

    print(f"Done: replayed={replayed}, peeked/skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

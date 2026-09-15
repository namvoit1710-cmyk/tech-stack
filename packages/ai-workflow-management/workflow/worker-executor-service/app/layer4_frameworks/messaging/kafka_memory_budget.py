"""Explicit librdkafka buffer bounds — SA-2266 (P3-2).

The audit finding
-----------------
``KafkaBrokerClient`` created its ``Producer`` with nothing but
``bootstrap.servers``, and its ``Consumer`` with only the group/offset/commit
keys. Every buffer therefore ran at librdkafka's **defaults**, which are sized
for a server, not for a 256MB pod:

============================== ================== ==========================
property                       librdkafka default what it means here
============================== ================== ==========================
queue.buffering.max.kbytes     1 048 576 (1 GiB)  produce backlog before the
                                                  client starts refusing —
                                                  four times the whole pod
queue.buffering.max.messages   100 000            message count on that queue
queued.max.messages.kbytes     65 536 (64 MiB)    pre-fetched consumer backlog
fetch.message.max.bytes        1 048 576 (1 MiB)  per-partition fetch size
receive.message.max.bytes      100 000 000        single response ceiling
============================== ================== ==========================

These are C-side allocations: they do not appear in any Python-level cache
metric, and nothing in the service bounded them. A produce burst that outpaces
the broker can therefore grow ~1 GiB of native buffer on a pod capped at 256MB
— an OOM by configuration rather than by code.

(Key names and their accepted ranges were verified against the installed
librdkafka 2.13.0 by feeding it out-of-range values and reading back its own
error text; the default column is librdkafka's documented default.)

What this module does
---------------------
Derives a bound for each buffer from one budget, and returns it as ordinary
config. It is applied UNDER the caller's ``extra_config``, so an operator can
still tune any single key without editing code — the goal is that the values
are *chosen*, not that they are fixed.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, "")
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        logger.warning("%s=%r is not an integer — using %d", name, raw, default)
        return max(minimum, default)


# Total native broker buffer allowed across produce + consume. 32MB is 12.5% of
# a 256MB pod: enough to ride a burst, small enough to leave room for the work.
KAFKA_BUFFER_BUDGET_BYTES = _int_env("KAFKA_BUFFER_BUDGET_BYTES", 32 * 1024 * 1024)
# Average produced message size ESTIMATE, used only to derive a message-count
# companion to the byte cap. Named "estimate" because that is what it is.
KAFKA_MESSAGE_SIZE_ESTIMATE_BYTES = _int_env(
    "KAFKA_MESSAGE_SIZE_ESTIMATE_BYTES", 4096,
)
# librdkafka floor: receive.message.max.bytes must exceed fetch.max.bytes by at
# least 512 bytes, and may not go below 1000.
_RECEIVE_HEADROOM_BYTES = 512
_RECEIVE_MIN_BYTES = 1000


def _half_budget_kbytes() -> int:
    """Produce and consume each get half of the budget, in KiB."""
    return max(1, (KAFKA_BUFFER_BUDGET_BYTES // 2) // 1024)


def producer_buffer_config() -> dict[str, int]:
    """Bounds for the produce path."""
    kbytes = _half_budget_kbytes()
    messages = max(
        1, (kbytes * 1024) // max(1, KAFKA_MESSAGE_SIZE_ESTIMATE_BYTES),
    )
    return {
        "queue.buffering.max.kbytes": kbytes,
        "queue.buffering.max.messages": messages,
    }


def consumer_buffer_config() -> dict[str, int]:
    """Bounds for the consume path.

    Three properties, easily confused, and getting them wrong is not a
    performance regression — librdkafka REFUSES to construct the consumer, so
    the pod does not boot:

    * ``fetch.message.max.bytes`` — largest single message, per partition
      (default 1 MiB). Left alone: shrinking it does not save steady-state
      memory, it makes large messages undeliverable.
    * ``fetch.max.bytes`` — total bytes in ONE fetch response across all
      partitions (default **50 MiB**). This is a real per-request allocation
      and is bounded here.
    * ``receive.message.max.bytes`` — hard ceiling on any single response.
      librdkafka enforces ``receive.message.max.bytes >= fetch.max.bytes + 512``
      and rejects the config otherwise.
    """
    kbytes = _half_budget_kbytes()
    fetch_message_max = _int_env("KAFKA_FETCH_MESSAGE_MAX_BYTES", 1024 * 1024)
    fetch_max = _int_env("KAFKA_FETCH_MAX_BYTES", 8 * 1024 * 1024)
    # The invariant librdkafka checks. Derived, never hand-typed, so a change
    # to fetch_max cannot silently break construction.
    receive_max = max(
        _RECEIVE_MIN_BYTES,
        fetch_max + _RECEIVE_HEADROOM_BYTES,
        fetch_message_max + _RECEIVE_HEADROOM_BYTES,
    )
    # The backlog cannot be smaller than one fetch, or the consumer stalls.
    queued_kbytes = max(kbytes, (fetch_message_max // 1024) + 1)
    return {
        "queued.max.messages.kbytes": queued_kbytes,
        "fetch.message.max.bytes": fetch_message_max,
        "fetch.max.bytes": fetch_max,
        "receive.message.max.bytes": receive_max,
    }


def apply_buffer_bounds(conf: dict, role: str) -> dict:
    """Return *conf* with budget bounds filled in where the caller left gaps.

    The caller always wins: these are defaults for keys nobody set, not an
    override. A service that has measured its own workload should be able to
    say so without this module arguing.
    """
    bounds = producer_buffer_config() if role == "producer" else consumer_buffer_config()
    merged = dict(bounds)
    merged.update(conf)
    return merged

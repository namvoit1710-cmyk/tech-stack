"""SMDG CS Worker — entry point (multi-type PULL / SERVER).

Registers the ``cs_*`` node types from ``app/node_types.py``. Every mode goes through the
stock ``run_worker(node_types=...)``: SERVER, HEADLESS and PULL all handle multi-type
natively since PR #2007.

PULL used to need a private copy of the lease loop here (``app/pull_multi.py``), copied
from log-worker because the SDK's pull runner was single-type. That copy is gone. It also
cost real latency: the fork walked its node types in ONE round robin, long-polling each in
turn, so with eight types a node waited for its slot in the cycle. Measured on the log
flow, 2026-08-24: each fetch node took 17–22 s wall-clock for ~2.5 s of actual OData work.
The SDK's runner gives every node type its own lease loop, so a task is picked up on the
long poll that is already open for it.

``WORKER_MAX_CONCURRENT`` is pinned in docker-compose.yml because that changes meaning
with the fork gone: it is now the cap on total in-flight tasks ACROSS all node types, and
the SDK's runner is otherwise uncapped. It is what keeps concurrent Claude calls at or
below the number of tokens supplied.

The APP_MODE branch that used to live here is gone too, and with it a bug worth
remembering: it read the mode with ``os.getenv``, but pydantic also resolves APP_MODE from
a ``.env`` file, and a dotenv value never reaches ``os.environ``. With ``APP_MODE=PULL`` in
``.env`` the branch saw "SERVER", called ``run_worker()``, which then read
``settings.APP_MODE == "PULL"`` and dispatched to the SDK's SINGLE-type pull runner —
which demanded an ``execute_task`` use case this worker deliberately has none of, and
died. ``run_worker`` now owns that decision end to end.
"""

import os

from worker_sdk import run_worker

from app.config import settings as app_settings
from app.node_types import NODE_TYPES
from app.registry import REGISTRY


def _announce_registry() -> None:
    """Say out loud what this container can reach. A worker that starts clean but serves
    no project is otherwise indistinguishable from a healthy one until a user asks."""
    catalogue = REGISTRY.catalogue()
    print(f"[cs-worker] targets: {catalogue or '(none)'}")
    for problem in REGISTRY.problems():
        print(f"[cs-worker] NOT usable — {problem}")
    if not catalogue:
        print("[cs-worker] no usable target. Set $LOG_WORKER_CLIENT_ID_<PROJECT>_<ENV>, "
              "$LOG_WORKER_CLIENT_SECRET_<PROJECT>_<ENV>, $LOG_WORKER_APP_PREFIX_<PROJECT>_<ENV> "
              "and $LOG_WORKER_REGION_<PROJECT>_<ENV>, then restart.")


if __name__ == "__main__":
    _announce_registry()

    # No compiled features — a real (existing but empty) dir so the scanner finds nothing.
    run_worker(
        app_settings=app_settings,
        features_path=os.path.join(os.path.dirname(__file__), "app", "features"),
        base_module="app.features",
        node_types=NODE_TYPES,
    )

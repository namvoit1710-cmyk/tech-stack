"""SMDG Log Worker — entry point (multi-type PULL / SERVER).

Registers two self-authenticating log-fetcher node types (see ``app/node_types.py``):
``request_action_log`` and ``submit_event_log``. Each node OAuth-logs-in
(client_credentials, creds from the local secrets file) then GETs a CommonProcessService
OData endpoint by reqID. All endpoint knobs are configurable node fields.

Every mode goes through the stock ``run_worker(node_types=...)``: SERVER, HEADLESS and
PULL all handle multi-type natively now.

PULL used to need a private copy of the lease loop here (``app/pull_multi.py``) because
the SDK's pull runner was single-type. That copy is gone; its concurrency cap came with
it into the SDK, and ``WORKER_MAX_CONCURRENT`` is pinned to 1 in docker-compose.yml to
keep the behaviour this worker has always had (the fork's default was one task at a
time; the SDK's per-type loops would otherwise run all three concurrently).

The APP_MODE branch that used to live here is gone too, and with it a bug worth
remembering: it read the mode with ``os.getenv``, but pydantic also resolves APP_MODE
from a ``.env`` file, and a dotenv value never reaches ``os.environ``. With
``APP_MODE=PULL`` in ``.env`` the branch saw "SERVER", called ``run_worker()``, which
then read ``settings.APP_MODE == "PULL"`` and dispatched to the SDK's SINGLE-type pull
runner — which demanded an ``execute_task`` use case this worker deliberately has none
of, and died. ``run_worker`` now owns that decision end to end, and its multi-type
dispatch happens before the ``execute_task`` check.
"""

import os

from worker_sdk import run_worker

from app.config import settings as app_settings
from app.node_types import NODE_TYPES


if __name__ == "__main__":
    # No compiled features — a real (existing but empty) dir so the scanner finds nothing.
    run_worker(
        app_settings=app_settings,
        features_path=os.path.join(os.path.dirname(__file__), "app", "features"),
        base_module="app.features",
        node_types=NODE_TYPES,
    )

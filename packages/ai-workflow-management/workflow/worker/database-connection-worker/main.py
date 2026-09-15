"""Database Connection Worker - Entry point.

This worker executes database operations (SELECT, INSERT, UPDATE, DELETE)
against PostgreSQL, MySQL, and SAP HANA databases with:
- Connection pooling via SQLAlchemy 2.0+
- Security validation for SQL queries
- Resource limits (max rows, statement timeout)
"""

import os

from worker_sdk import run_worker
from app.layer4_frameworks.config import settings as app_settings
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    DATABASE_CONNECTION_INPUT_SCHEMA,
    DATABASE_CONNECTION_OUTPUT_SCHEMA,
)
from app.functions import DATABASE_WORKER_FUNCTIONS


if __name__ == "__main__":
    run_worker(
        app_settings=app_settings,
        features_path=os.path.join(os.path.dirname(__file__), "app", "layer2_application", "features"),
        base_module="app.layer2_application.features",
        extra_dependencies={
            "input_schema": DATABASE_CONNECTION_INPUT_SCHEMA,
            "output_schema": DATABASE_CONNECTION_OUTPUT_SCHEMA,
        },
        functions=DATABASE_WORKER_FUNCTIONS,
    )

# SA-1530: rebuild for worker-sdk WORKER_MAX_CONCURRENT backpressure gate

# SA-1977: rebuild — pick up the worker-sdk streaming converter, which now
# declares value_kind/item_kind on a streamed file_ref (and unpacks the native
# producer's new 4-tuple). WORKER_CHUNKED_OUTPUT_ENABLED is TRUE on tenant-1, so
# this path is live: without a rebuild the worker keeps the old SDK and streams
# outputs with no declaration, leaving SA-1977 only half-effective.

# SA-2047 R14: rebuild — pick up the worker-sdk that sends worker_app (the CF
# application_name) and, with it, the R8 instance identity these images never
# received. Without this touch the SDK change ships and no worker runs it
# (ci-worker-sdk-rebuild-gap): worker-sdk-only changes do NOT rebuild dependent
# workers.

# SA-1905: rebuild — pick up the nested Arrow sidecar. Two shared things changed
# and NEITHER rebuilds a worker on its own:
#
#   * ``libs/json-csv-streamer`` — the native wheel. Every worker's Dockerfile
#     builds it from that path (``deployment.yml``: ``buildContexts:
#     json_csv_streamer``), and WORKER_CHUNKED_OUTPUT_ENABLED is TRUE here, so
#     this is the wheel that writes real node output today.
#   * ``worker-sdk`` — ``StreamingOutputConverter`` gained an ``arrow_sidecar``
#     flag (default off) and now emits ``arrow_file_id`` on the ref when one was
#     written.
#
# ``reusable-discover.yml:360`` matches a changed file against a service's OWN
# app_dir by prefix, and both of those are sibling directories — so without this
# touch the control plane ships on the new wheel while all ten workers keep the
# old one. The flags are off either way; what this buys is that the two sides
# are running the same code when someone turns them on.

# SA-1910 rebuild marker — worker-sdk's StreamingOutputConverter now splits a
# DICT output into the nested-array CSV tree (json_csv_streamer.jsonl_split_recursive)
# and reports every file_id it uploads as ``_uploaded_file_ids``.
#
# Same reason as the SA-1905 marker above: ``reusable-discover.yml:360`` matches a
# changed file against a service's OWN app_dir by prefix, so a worker-sdk-only
# change rebuilds nothing here. Without this touch a dict output keeps riding an
# event inline and keeps dying at the 1 048 576-byte broker cap, which is the
# exact failure this Story removes.

# Rebuild marker, 2026-08-21 — pick up the worker-sdk changes that landed after the
# SA-1910 marker above. Eight of these ten images were still on ``solace-1024``
# today, thirteen days behind, because ``reusable-discover.yml`` matches a changed
# file against a service's OWN appDir by prefix (``[[ "$file" == "$svc_app_dir"* ]]``)
# and ``worker-sdk`` is a sibling directory: a buildContext for every worker, a
# trigger for none.
#
#   * ``9127ba295`` — lease renewal is bounded. This is the one that matters. The
#     executor's recovery net is ``sweep_expired_leases``, which only acts on rows
#     where ``state == LEASED and deadline < now``. An uncapped renewer keeps
#     pushing the deadline, so that condition is never true: ``attempt_count`` never
#     increments, ``MAX_LEASE_ATTEMPTS`` is never reached, and a hung task is never
#     re-enqueued or POISONED. On an un-rebuilt worker, stuck stays stuck.
#   * ``843d97d45`` — the result is weighed at the worker, with per-field
#     attribution, instead of being discovered at the broker cap.
#   * ``74b39ac9f`` — a large string output ships as a pointer rather than inline.

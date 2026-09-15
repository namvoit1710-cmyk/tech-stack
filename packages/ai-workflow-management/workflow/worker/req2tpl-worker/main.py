"""req2tpl Worker — entry point (MULTI-TYPE, jira-worker / SA-1078 pattern).

Nine nodes over the local ``req2tpl`` Rust CLI (see ``app/node_types.py``).

Normally run with ``APP_MODE=PULL`` against a remote executor, because the CLI it
drives is a per-machine build and the workbooks it reads are on that machine's
disk. SERVER and HEADLESS work too — the stock ``run_worker(node_types=...)``
handles all three natively since the SDK took over multi-type PULL, so there is
no private lease loop here.
"""

import os

from worker_sdk import run_worker

from app.layer4_frameworks.config import settings as app_settings
from app.node_types import NODE_TYPES


if __name__ == "__main__":
    run_worker(
        app_settings=app_settings,
        features_path=os.path.join(
            os.path.dirname(__file__), "app", "layer2_application", "features"
        ),
        base_module="app.layer2_application.features",
        node_types=NODE_TYPES,
    )

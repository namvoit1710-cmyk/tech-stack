"""Configuration for the req2tpl worker.

A LOCAL, PULL-mode worker (claude-worker pattern) that wraps the ``req2tpl`` Rust
CLI — the deterministic requirement + FS -> SimpleMDG import-template builder.

The tool lives outside this repo and is built per-machine, so its location is an
env var rather than a bundled path. Nothing here reaches the network except the
two Jira nodes, which authenticate from the vault (never from disk).
"""

from pydantic_settings import SettingsConfigDict

from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    """req2tpl worker settings.

    MULTI-TYPE: ``WORKER_TYPE`` below is only the process identity / single-type
    fallback. Every node registers its own ``req2tpl_*`` worker_type from
    ``app/node_types.py`` (see ``NODE_TYPES``).
    """

    # extra="ignore" so an .env carrying unrelated keys does not abort startup.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "req2tpl Worker"
    WORKER_NAME: str = "req2tpl"
    WORKER_TYPE: str = "req2tpl"
    WORKER_DESCRIPTION: str = (
        "LOCAL worker driving the req2tpl CLI: pull a ticket's workbooks from Jira, "
        "survey/init/map/build/verify a SimpleMDG import template, and attach the "
        "result back to the ticket. Deterministic, offline apart from the Jira calls."
    )

    WORKER_NODE_CLASS: str = "TECHNICAL"
    WORKER_ICON: str = "FileSpreadsheet"
    WORKER_COLOR: str = "#B7410E"  # rust
    WORKER_TAGS: str = "req2tpl,smdg,template,xlsx,local"

    # 35000/35001/36000/36010/37000/38000/38001/38002 are taken by other workers.
    # 360xx is the local-worker band (claude 36000, log 36010).
    SERVER_PORT: int = 36020

    # --- req2tpl CLI -----------------------------------------------------------
    # Absolute path to the built binary. Blank = look for `req2tpl` on PATH.
    REQ2TPL_BIN: str = ""
    # Root every run directory is confined under. A node may only name a path
    # BELOW this root — see app/layer2_application/services/workspace.py. This is
    # the whole reason a workflow on a shared tenant cannot write across this
    # machine's disk.
    REQ2TPL_WORKSPACE: str = ""
    # Per-invocation ceiling. `build` on the reference object runs in seconds;
    # this is a runaway guard, not a tuning knob.
    REQ2TPL_TIMEOUT_SECONDS: float = 300.0
    # Refuse to download an attachment larger than this (bytes). The workbooks
    # seen so far are 40 KB - 200 KB.
    REQ2TPL_MAX_ATTACHMENT_BYTES: int = 64 * 1024 * 1024

    # How many Jira file transfers run at once. Measured on the first live run:
    # three sequential downloads cost 8.2s and three sequential uploads 8.3s, on
    # a 35.6s pipeline — nearly half the wall clock was one-at-a-time HTTP. These
    # are independent transfers to one host, so a small pool is the whole fix.
    # Kept small deliberately: Jira rate-limits, and the point is to overlap
    # latency, not to flood.
    REQ2TPL_JIRA_CONCURRENCY: int = 4

    # --- PULL delivery ---------------------------------------------------------
    # The SDK default is FALSE, and a task outliving LEASE_TTL (60s) without
    # renewal gets re-leased by another instance => double execution, and the
    # original's callback is rejected on the dead lease token. A Jira download
    # over a slow link can cross 60s, so renewal is on here. main.py merges these
    # into the SDK singleton that the pull loop reads.
    LEASE_RENEW_ENABLED: bool = True
    LEASE_RENEW_MIN_INTERVAL_SECONDS: float = 5.0


settings = Settings()

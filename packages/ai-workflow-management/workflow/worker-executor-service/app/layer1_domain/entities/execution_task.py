from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


@dataclass
class ExecutionTask:
    """A task received from the workflow control plane for execution."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    node_id: str = ""
    worker_type: str = ""
    # SA-1490 Phase 1: pinned worker contract version (from WCP dispatch).
    # Plumbed here for Phase 3 version-aware routing; not yet consulted.
    spec_version: str = ""
    # SA-2424: optional per-node instance pin (debug routing). When non-empty the
    # executor dispatches ONLY to this worker instance (by instance_id or full
    # registration id) and fails fast if it is not eligible — never silently
    # picking another replica. Blank = normal freshest-healthy selection.
    target_instance_id: str = ""
    # SA-2437: host label for the pin — routing fallback when target_instance_id
    # is stale/absent (match a live registration of this worker_type by host).
    target_host: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    input_refs: dict[str, str] = field(default_factory=dict)
    node_config: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 30000
    retry_policy: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    attempt_count: int = 0
    assigned_worker: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    completed_at: str | None = None

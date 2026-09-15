"""Frozen-at-run-start workflow snapshot.

A ``WorkflowSnapshot`` captures the workflow definition that a run executes
against. Once a run is started, all engine reads of the workflow go through
the snapshot — never the live ``workflow_repo`` — so mid-run edits to the
workflow cannot leak into in-flight runs.

The snapshot is stored in its own ``workflow_snapshots`` table (one row per
capture) and referenced by ``WorkflowRun.workflow_snapshot_id``. Test runs
re-snapshot on every ``/test/full`` trigger to pick up the latest edits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class WorkflowSnapshot:
    """Immutable record of a workflow definition at a point in time.

    ``definition`` is the JSON-safe payload produced by
    ``Workflow.to_snapshot_dict``; reconstruct via
    ``Workflow.from_snapshot_dict(snapshot.definition)``.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str = ""
    workflow_version: str = ""
    definition: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

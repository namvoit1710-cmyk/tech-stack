"""SA-2437 — per-workflow worker-instance configuration (debug routing).

One row per flow (workflow) id. Persists the user's worker-instance pin so it
survives reloads / other machines and is applied on EVERY run entry path
(StartRun + TestFull + triggers), not just FE-initiated runs. Replaces the v1
FE-localStorage approach (SA-2424).

``selection`` shape — each pin carries both the routing key (instance_id) and a
human label (host, from the /workers page):

    {
      "by_worker_type": {"<worker_type>": {"instance_id": "...", "host": "..."}},
      "by_node":        {"<node_id>":    {"instance_id": "...", "host": "..."}},
    }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowWorkerConfig:
    workflow_id: str = ""
    tenant_id: str = "system"
    mode: str = "WHOLE_FLOW"  # "WHOLE_FLOW" | "PER_NODE"
    selection: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""
    updated_by: str = ""

"""Centralized event enricher for the WORKFLOW_EVENT_TOPIC.

Adds run, workflow, and node metadata to the envelope so external
consumers receive full context without additional API calls. Task input
and output are intentionally NOT enriched: they are unbounded for
upload-style nodes and pushed the envelope past the Event Mesh 900 KB
cap. Consumers that need task details can fetch on demand via ``task_id``.

Workflow metadata is LRU-cached to avoid repeated DB lookups within a burst.
That sentence was aspirational until M4 (SA-2261): the cache was a plain dict
keyed by workflow_id with no cap and no TTL, which leaked one entry per
distinct workflow for the life of the pod AND — the worse half — kept serving
the pre-edit (workflow, metadata) forever once a workflow had been enriched.
It is now genuinely LRU-capped and TTL'd, so an edit shows up within the TTL.

All enrichment is best-effort: failures never block event publishing.
Node/workflow enrichment misses are logged at warning level because
``_node`` (name + node_type) is part of the centralized-topic contract.
"""

from __future__ import annotations

import logging
from typing import Any

from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer4_frameworks.caching.bounded_ttl_cache import (
    MISS,
    BoundedTTLCache,
    CacheStats,
    env_int,
)

logger = logging.getLogger(__name__)

# M4 (SA-2261). The TTL mirrors NodeCatalogCache's 30s: short enough that a
# workflow edit becomes visible on its own, long enough to absorb a burst.
# The cap is the memory guarantee — workflow_id is an unbounded key space.
_WORKFLOW_CACHE_MAX = env_int("EVENT_ENRICHER_WORKFLOW_CACHE_MAX", 512)
_WORKFLOW_CACHE_TTL_S = env_int("EVENT_ENRICHER_WORKFLOW_CACHE_TTL_S", 30, minimum=1)


class CentralizedEventEnricher:
    """Enrich a centralized event envelope with run/workflow/node metadata."""

    def __init__(
        self,
        workflow_repo: IWorkflowRepository | None = None,
        run_repo: IRunRepository | None = None,
        task_repo: ITaskRepository | None = None,
        workflow_cache: BoundedTTLCache | None = None,
    ) -> None:
        self._workflow_repo = workflow_repo
        self._run_repo = run_repo
        # Cache: workflow_id → (Workflow object, {name, description, version}).
        # LRU-capped so a pod that sees many workflows cannot grow without
        # bound, and TTL'd so an edited workflow stops being served stale.
        # Injectable so the TTL can be tested against a hand-cranked clock
        # rather than a sleep.
        self._workflow_cache = workflow_cache if workflow_cache is not None else BoundedTTLCache(
            maxsize=_WORKFLOW_CACHE_MAX,
            ttl_seconds=_WORKFLOW_CACHE_TTL_S,
            name="event_enricher_workflow",
        )

    def enrich(self, envelope: dict) -> dict:
        """Add _run, _workflow, _node sub-dicts to the envelope in-place."""
        self._enrich_run_info(envelope)
        self._enrich_workflow_info(envelope)
        self._enrich_node_info(envelope)
        return envelope

    # ------------------------------------------------------------------
    # Run metadata
    # ------------------------------------------------------------------

    def _enrich_run_info(self, envelope: dict) -> None:
        run_id = envelope.get("run_id", "")
        if not run_id or not self._run_repo:
            return
        try:
            run = self._run_repo.get_by_id(run_id)
            if run:
                envelope["_run"] = {
                    "status": run.status.value if hasattr(run.status, "value") else str(run.status),
                    "correlation_id": run.correlation_id or "",
                    "tenant_id": run.tenant_id or "",
                    "parent_run_id": run.parent_run_id or "",
                    "depth_level": run.depth_level,
                    "started_at": run.started_at or "",
                    "is_test": run.is_test,
                }
                self._stamp_root_workflow_id(envelope, run)
        except Exception as exc:
            logger.debug("Run enrichment failed for %s: %s", run_id, exc)

    def _stamp_root_workflow_id(self, envelope: dict, run: Any) -> None:
        """Set top-level ``root_workflow_id`` next to ``root_run_id``.

        The publisher overrides ``run_id`` with ``root_run_id`` before
        enrichment, so ``run`` is normally the root run already; the extra
        lookup only happens when the enricher is fed a child-run envelope
        directly.
        """
        root_id = envelope.get("root_run_id") or run.root_run_id or run.id
        if root_id == run.id:
            envelope["root_workflow_id"] = run.workflow_id
            return
        root_run = self._run_repo.get_by_id(root_id)
        if root_run:
            envelope["root_workflow_id"] = root_run.workflow_id

    # ------------------------------------------------------------------
    # Workflow metadata (cached)
    # ------------------------------------------------------------------

    def _get_workflow(self, workflow_id: str) -> tuple[Any, dict] | None:
        """Return (workflow_obj, metadata_dict) from cache or DB."""
        cached = self._workflow_cache.get(workflow_id)
        if cached is not MISS:
            return cached
        if not self._workflow_repo:
            return None
        try:
            wf = self._workflow_repo.get_by_id(workflow_id)
            if wf:
                meta = {
                    "name": wf.name,
                    "description": wf.description,
                    "version": wf.version,
                }
                self._workflow_cache.put(workflow_id, (wf, meta))
                return wf, meta
        except Exception as exc:
            logger.warning("Workflow enrichment failed for %s: %s", workflow_id, exc)
        # A miss is deliberately NOT cached: enrichment is best-effort and a
        # transient repo failure must not blind this pod until a TTL expires.
        return None

    def invalidate_workflow(self, workflow_id: str) -> bool:
        """Drop one workflow so the next enrichment re-reads it.

        The TTL already bounds staleness on its own; this is the hook for
        wiring an explicit ``workflow.changed`` event when one exists.
        """
        return self._workflow_cache.invalidate(workflow_id)

    def cache_stats(self) -> CacheStats:
        """Bucket snapshot for the SA-2256 memory-budget export."""
        return self._workflow_cache.stats()

    def _enrich_workflow_info(self, envelope: dict) -> None:
        wf_id = envelope.get("workflow_id", "")
        if not wf_id:
            return
        result = self._get_workflow(wf_id)
        if result:
            envelope["_workflow"] = result[1]

    # ------------------------------------------------------------------
    # Node metadata
    # ------------------------------------------------------------------

    def _enrich_node_info(self, envelope: dict) -> None:
        node_id = envelope.get("node_id", "")
        wf_id = envelope.get("workflow_id", "")
        if not node_id or not wf_id:
            return
        result = self._get_workflow(wf_id)
        if not result:
            return
        wf = result[0]
        try:
            node = wf.get_node(node_id)
            if node:
                envelope["_node"] = {
                    "name": node.name,
                    "node_type": node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
                    "description": node.description,
                    "worker_type": node.worker_type or "",
                }
            else:
                logger.warning(
                    "Node enrichment skipped: node %s not found in workflow %s — "
                    "centralized envelope will lack _node name/type", node_id, wf_id,
                )
        except Exception as exc:
            logger.warning("Node enrichment failed for %s/%s: %s", wf_id, node_id, exc)


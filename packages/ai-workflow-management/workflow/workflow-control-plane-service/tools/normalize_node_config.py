"""One-time node-config normalization (NodeSpec redesign, Phase 6).

Re-homes legacy node configuration to the canonical NodeSpec layout —
the clean-break migration that lets the Phase 3 handlers (canonical
keys only, no fallbacks) read every stored workflow. Idempotent: a
second run produces an empty diff.

Per design §9 (docs/design/node-spec-single-source.md):

* MERGE     — ``wait_strategy``→``merge_strategy``; values
              ``wait_all/wait_any/wait_n``→``all/any/n_of_m``;
              ``n_required``→``wait_count``
* LOOP      — ``items_expression``→``collection``
* WORKFLOW  — config ``input_binding``→``input_mapping`` (rows fold to
              a dict at split)
* PARALLEL  — dead ``max_concurrency``/``fail_strategy`` dropped
              (preserved in workflow ``metadata._migration_dropped``)
* CONDITION / SWITCH — ``data`` re-homes from input_binding to config
              (via re-split); when both ``data`` and ``expression`` are
              set, ``expression`` is dropped (matches old precedence)
* TRIGGER   — ``webhook_path``/``event_filter`` re-home to config
              (via re-split)
* pending   — JSON-string ``input_schema`` parsed once
* all       — ``config._node_definition`` + ``config._node_internals``
              → ``definition_snapshot``; then the merged parameters are
              re-split through the node's spec, so every stray key
              lands where the spec says it belongs
* all       — residual SpecViolations are REPORTED, never auto-fixed

Usage (from the service root, with the target backend configured via
the same env the service uses):

    python -m tools.normalize_node_config            # report only
    python -m tools.normalize_node_config --apply    # write changes
    python -m tools.normalize_node_config --skip-snapshots

The report also answers design §11.3 (SWITCH ``cases``/``default``
liveness) and counts JSON-string schemas (gates the Phase 4 chain cut).
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from app.layer1_domain.node_spec import get_spec
from app.layer1_domain.node_spec.parameter_mapper import (
    merge_parameters,
    split_parameters,
)
from app.layer1_domain.node_spec.validation import validate_node
from app.layer1_domain.value_objects.node_type import NodeType

_MERGE_VOCAB = {"wait_all": "all", "wait_any": "any", "wait_n": "n_of_m"}
_PARALLEL_DEAD_KEYS = ("max_concurrency", "fail_strategy")
_JSON_SCHEMA_TYPES = {"INPUT", "HUMAN_ACTION", "DATA_EDIT"}


# ──────────────────────────── report ────────────────────────────


@dataclass
class Report:
    workflows_seen: int = 0
    workflows_changed: int = 0
    snapshots_seen: int = 0
    snapshots_changed: int = 0
    nodes_changed: int = 0
    transforms: dict[str, int] = field(default_factory=dict)
    # design §11.3 — SWITCH legacy expression/cases/default in live data
    switch_legacy_nodes: list[str] = field(default_factory=list)
    json_string_schemas: list[str] = field(default_factory=list)
    unparseable_schemas: list[str] = field(default_factory=list)
    residual_violations: list[dict[str, str]] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)

    def bump(self, transform: str) -> None:
        self.transforms[transform] = self.transforms.get(transform, 0) + 1

    def summary(self) -> str:
        lines = [
            f"workflows: {self.workflows_changed}/{self.workflows_seen} changed",
            f"snapshots: {self.snapshots_changed}/{self.snapshots_seen} changed",
            f"nodes changed: {self.nodes_changed}",
            "transforms:",
        ]
        for name, count in sorted(self.transforms.items()):
            lines.append(f"  {name}: {count}")
        lines.append(
            f"SWITCH legacy expression/cases/default nodes (§11.3): "
            f"{len(self.switch_legacy_nodes)}"
            + (f" → {self.switch_legacy_nodes[:10]}" if self.switch_legacy_nodes else "")
        )
        lines.append(f"JSON-string input_schema parsed: {len(self.json_string_schemas)}")
        if self.unparseable_schemas:
            lines.append(f"UNPARSEABLE input_schema (manual fix!): {self.unparseable_schemas}")
        lines.append(f"residual validation violations: {len(self.residual_violations)}")
        for v in self.residual_violations[:25]:
            lines.append(f"  {v}")
        return "\n".join(lines)


# ──────────────────────────── pure node transform ────────────────────────────


def normalize_node_dict(
    node: dict[str, Any], *, where: str, report: Report,
) -> bool:
    """Normalize one node payload in place. Returns True when changed.

    ``node`` is the entity/snapshot dict shape: ``node_type``, ``config``,
    ``input_binding``, ``parameters``, ``definition_snapshot``.
    """
    node_type = str(node.get("node_type") or "TASK")
    node_id = str(node.get("id") or "?")
    label = f"{where}/{node_id}"
    before = copy.deepcopy(
        {k: node.get(k) for k in (
            "config", "input_binding", "parameters", "definition_snapshot",
        )}
    )

    config = dict(node.get("config") or {})
    binding = dict(node.get("input_binding") or {})

    # 1. definition snapshots out of config (Phase 4 shape).
    nd_def = config.pop("_node_definition", None)
    nd_internals = config.pop("_node_internals", None)
    if (nd_def or nd_internals) and not node.get("definition_snapshot"):
        snapshot: dict[str, Any] = {}
        if isinstance(nd_def, dict):
            snapshot.update(nd_def)
        if isinstance(nd_internals, dict):
            snapshot.update(nd_internals)
        node["definition_snapshot"] = snapshot
        report.bump("definition_snapshot_extracted")

    # 2. merge to the FE view (drops any other _-prefixed strays).
    merged = merge_parameters(config, binding)

    # 3. per-type key/value renames on the merged dict.
    if node_type == "MERGE":
        if "wait_strategy" in merged:
            merged.setdefault("merge_strategy", merged["wait_strategy"])
            del merged["wait_strategy"]
            report.bump("merge_wait_strategy_renamed")
        if "n_required" in merged:
            merged.setdefault("wait_count", merged["n_required"])
            del merged["n_required"]
            report.bump("merge_n_required_renamed")
        strategy = merged.get("merge_strategy")
        if strategy in _MERGE_VOCAB:
            merged["merge_strategy"] = _MERGE_VOCAB[strategy]
            report.bump("merge_vocabulary_normalized")

    elif node_type == "LOOP":
        if "items_expression" in merged:
            merged.setdefault("collection", merged["items_expression"])
            del merged["items_expression"]
            report.bump("loop_items_expression_renamed")

    elif node_type == "WORKFLOW":
        if "input_binding" in merged:
            merged.setdefault("input_mapping", merged["input_binding"])
            del merged["input_binding"]
            report.bump("workflow_input_binding_renamed")

    elif node_type == "PARALLEL":
        dropped = {k: merged.pop(k) for k in _PARALLEL_DEAD_KEYS if k in merged}
        if dropped:
            node.setdefault("_migration_dropped", {}).update(dropped)
            report.bump("parallel_dead_fields_dropped")

    elif node_type in ("CONDITION", "SWITCH"):
        has_data = bool(merged.get("data"))
        if has_data and merged.get("expression"):
            del merged["expression"]  # old precedence: structured wins
            report.bump(f"{node_type.lower()}_expression_dropped")
        if node_type == "SWITCH" and (
            merged.get("expression") or merged.get("cases") or merged.get("default")
        ):
            report.switch_legacy_nodes.append(label)

    if node_type in _JSON_SCHEMA_TYPES:
        raw_schema = merged.get("input_schema")
        if isinstance(raw_schema, str) and raw_schema.strip():
            try:
                merged["input_schema"] = json.loads(raw_schema)
                report.json_string_schemas.append(label)
                report.bump("json_string_schema_parsed")
            except (ValueError, TypeError):
                report.unparseable_schemas.append(label)

    # 4. re-split through the spec: every key lands where the spec says.
    try:
        spec = get_spec(NodeType(node_type))
    except ValueError:
        spec = get_spec(NodeType.TASK)
    new_config, new_binding = split_parameters(spec, merged)

    node["config"] = new_config
    node["input_binding"] = new_binding
    # 5. parameters mirror the normalized FE view when they were in use.
    if node.get("parameters"):
        node["parameters"] = dict(merged)

    # 6. residual violations — reported, never auto-fixed.
    for v in validate_node(spec, SimpleNamespace(id=label, config=new_config)):
        report.residual_violations.append(v.to_dict())

    after = {k: node.get(k) for k in (
        "config", "input_binding", "parameters", "definition_snapshot",
    )}
    changed = before != after
    if changed:
        report.nodes_changed += 1
        report.changes.append(label)
    return changed


# ──────────────────────────── adapters ────────────────────────────


def normalize_workflow_entity(workflow: Any, report: Report) -> bool:
    """Normalize a Workflow entity's nodes in place via the dict core."""
    changed = False
    dropped: dict[str, Any] = {}
    for node in workflow.nodes:
        payload = {
            "id": node.id,
            "node_type": (
                node.node_type.value
                if hasattr(node.node_type, "value") else str(node.node_type)
            ),
            "config": node.config,
            "input_binding": node.input_binding,
            "parameters": node.parameters,
            "definition_snapshot": node.definition_snapshot,
        }
        if normalize_node_dict(
            payload, where=f"workflow:{workflow.id}", report=report,
        ):
            changed = True
            node.config = payload["config"]
            node.input_binding = payload["input_binding"]
            node.parameters = payload["parameters"]
            node.definition_snapshot = payload["definition_snapshot"]
            if payload.get("_migration_dropped"):
                dropped[node.id] = payload["_migration_dropped"]
    if dropped:
        workflow.metadata = dict(workflow.metadata or {})
        workflow.metadata.setdefault("_migration_dropped", {}).update(dropped)
    return changed


def normalize_snapshot_definition(definition: dict[str, Any], report: Report) -> bool:
    """Normalize a run snapshot's frozen workflow definition in place."""
    changed = False
    for node in definition.get("nodes", []) or []:
        if normalize_node_dict(
            node, where=f"snapshot:{definition.get('id', '?')}", report=report,
        ):
            changed = True
            node.pop("_migration_dropped", None)
    return changed


# ──────────────────────────── runners ────────────────────────────


def run_workflows(workflow_repo: Any, report: Report, apply: bool) -> None:
    for workflow in workflow_repo.list():
        report.workflows_seen += 1
        # Dry-run must not mutate: the memory repo returns aliased
        # entity references, so in-place normalization would leak into
        # the store even without a save. Work on a copy when reporting.
        target = workflow if apply else copy.deepcopy(workflow)
        if not normalize_workflow_entity(target, report):
            continue
        report.workflows_changed += 1
        if apply:
            _save_with_retry(workflow_repo, target)


def run_snapshots(
    run_repo: Any, snapshot_repo: Any, report: Report, apply: bool,
) -> None:
    """Normalize the frozen definitions referenced by runs.

    Old runs' handlers read the snapshot, so the clean-break handlers
    need these normalized too (draining in-flight runs is unrealistic).
    """
    seen: set[str] = set()
    for run in _iter_runs(run_repo):
        snapshot_id = getattr(run, "workflow_snapshot_id", None)
        if not snapshot_id or snapshot_id in seen:
            continue
        seen.add(snapshot_id)
        snapshot = snapshot_repo.get_by_id(snapshot_id)
        if snapshot is None:
            continue
        report.snapshots_seen += 1
        definition = (
            snapshot.definition if apply else copy.deepcopy(snapshot.definition)
        )
        if not normalize_snapshot_definition(definition, report):
            continue
        report.snapshots_changed += 1
        if apply:
            snapshot_repo.save(snapshot)


def _iter_runs(run_repo: Any):
    """Page through every run via list_runs(filters)."""
    from app.layer2_application.repositories.run_repository_interface import (
        ListRunsFilter,
    )

    offset, limit = 0, 200
    while True:
        page = run_repo.list_runs(ListRunsFilter(offset=offset, limit=limit))
        items = getattr(page, "items", None) or []
        if not items:
            return
        yield from items
        offset += limit
        total = getattr(page, "total", None)
        if total is not None and offset >= total:
            return


def _save_with_retry(workflow_repo: Any, workflow: Any, attempts: int = 3) -> None:
    """CAS-aware save: re-read + re-normalize on version conflict."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            workflow_repo.save(workflow)
            return
        except Exception as exc:  # repo-specific conflict types
            last_exc = exc
            fresh = workflow_repo.get_by_id(workflow.id)
            if fresh is None:
                raise
            throwaway = Report()
            normalize_workflow_entity(fresh, throwaway)
            workflow = fresh
    raise last_exc  # type: ignore[misc]


# ──────────────────────────── CLI ────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write changes (default: dry-run report)")
    parser.add_argument("--skip-snapshots", action="store_true",
                        help="normalize workflows only")
    args = parser.parse_args(argv)

    from app.layer4_frameworks import create_infrastructure
    from app.layer4_frameworks.config.app_config import settings
    from app.layer4_frameworks.persistence import create_repositories

    infra = create_infrastructure(settings)
    repos = create_repositories(settings, getattr(infra, "db", None))
    print(f"backend: {repos.backend_name}  mode: "
          f"{'APPLY' if args.apply else 'dry-run'}")

    report = Report()
    run_workflows(repos.workflow, report, args.apply)
    if not args.skip_snapshots:
        run_snapshots(repos.run, repos.workflow_snapshot, report, args.apply)

    print(report.summary())
    if not args.apply and (report.workflows_changed or report.snapshots_changed):
        print("\nDry run — re-run with --apply to write the changes above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

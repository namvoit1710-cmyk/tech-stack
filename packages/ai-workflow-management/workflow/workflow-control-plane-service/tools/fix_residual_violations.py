"""Fix the residual SpecViolations left by ``normalize_node_config``.

``normalize_node_config`` re-homes legacy config to the canonical NodeSpec
layout but, by design (§9), only *reports* the violations that remain — it
never invents values. This tool closes the gap for the violation classes
that CAN be fixed deterministically, and surfaces the rest so a human can
resolve them.

What it fixes
-------------
* ``invalid_enum`` (auto, on by default) — when a field's stored value is
  the Choice *label* rather than its canonical *value* (e.g. TRIGGER
  ``trigger_type='Manual'`` → ``'manual'``, ``'Webhook'`` → ``'webhook'``,
  DATA_EDIT ``data_mode='Single Row'`` → ``'csv_single'``). Matches the
  stored value case-insensitively against each Choice's value, then its
  label; rewrites to the canonical value. Skips ``{{…}}`` templates.

What it can only fix WITH YOUR INPUT
------------------------------------
* ``missing_required`` — a script cannot invent a real ``workflow_id``,
  ``file_id`` or ``use_agent`` (they reference real entities). Supply a
  value per ``(node_type, key)`` in ``REQUIRED_DEFAULTS`` below and pass
  ``--apply-required-defaults`` to write them. Display-only text fields
  (HUMAN_ACTION ``instruction``) ship with a safe generic default.
  Anything without a configured default is left untouched and listed
  under "still missing" so you can fill them in the editor.

What it deliberately leaves alone
---------------------------------
* ``exclusive_group`` at severity ``warn`` (CONDITION/SWITCH with neither
  ``data`` nor ``expression``) — a legitimate draft state, not an error.
* ``UNPARSEABLE input_schema`` — the stored JSON string is corrupt; it is
  reported for manual repair (``--blank-unparseable-schema`` will null it).

Usage (from the service root, same backend env as the service):

    python -m tools.fix_residual_violations                       # dry-run report
    python -m tools.fix_residual_violations --apply               # write enum fixes
    python -m tools.fix_residual_violations --apply \
        --apply-required-defaults                                 # + REQUIRED_DEFAULTS
    python -m tools.fix_residual_violations --skip-snapshots
"""

from __future__ import annotations

import argparse
import copy
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from app.layer1_domain.node_spec import get_spec
from app.layer1_domain.node_spec.validation import validate_node
from app.layer1_domain.value_objects.node_type import NodeType

# ── Defaults for missing-required fields (opt-in, --apply-required-defaults) ──
#
# Key: (NODE_TYPE, config_key). Value: the value to write when the field is
# required, visible and empty. Only fields with a SAFE machine default belong
# here. workflow_id / file_id / use_agent are intentionally absent: there is
# no correct value a script can choose — they are listed under "still missing"
# for a human to resolve. Add an entry here only if you truly want every such
# node to get the same value.
REQUIRED_DEFAULTS: dict[tuple[str, str], Any] = {
    ("HUMAN_ACTION", "instruction"): "Please review and take action.",
    # ("WORKFLOW", "workflow_id"): "wf-REPLACE-ME",     # uncomment to force
    # ("DATA_EDIT", "file_id"): "{{$upstream.__file_id}}",
    # ("FILE_UPLOAD", "use_agent"): "agent-1",
}


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _is_template(value: Any) -> bool:
    return isinstance(value, str) and "{{" in value


# ──────────────────────────── report ────────────────────────────


@dataclass
class Report:
    workflows_seen: int = 0
    workflows_changed: int = 0
    snapshots_seen: int = 0
    snapshots_changed: int = 0
    nodes_changed: int = 0
    fixes: dict[str, int] = field(default_factory=dict)
    enum_fixes: list[str] = field(default_factory=list)
    default_fixes: list[str] = field(default_factory=list)
    schemas_blanked: list[str] = field(default_factory=list)
    still_missing: list[dict[str, str]] = field(default_factory=list)
    residual: list[dict[str, str]] = field(default_factory=list)

    def bump(self, name: str) -> None:
        self.fixes[name] = self.fixes.get(name, 0) + 1

    def summary(self) -> str:
        lines = [
            f"workflows: {self.workflows_changed}/{self.workflows_seen} changed",
            f"snapshots: {self.snapshots_changed}/{self.snapshots_seen} changed",
            f"nodes changed: {self.nodes_changed}",
            "fixes applied:",
        ]
        for name, count in sorted(self.fixes.items()):
            lines.append(f"  {name}: {count}")
        if self.enum_fixes:
            lines.append(f"enum label→value remaps ({len(self.enum_fixes)}):")
            lines.extend(f"  {e}" for e in self.enum_fixes[:25])
        if self.default_fixes:
            lines.append(f"required-default fills ({len(self.default_fixes)}):")
            lines.extend(f"  {e}" for e in self.default_fixes[:25])
        if self.schemas_blanked:
            lines.append(f"input_schema blanked: {self.schemas_blanked}")
        # The honest part: what no script could decide.
        lines.append(
            f"STILL MISSING (need a human value): {len(self.still_missing)}"
        )
        for v in self.still_missing[:40]:
            lines.append(f"  {v['node_id']}  {v['key']}")
        lines.append(f"residual violations after fix: {len(self.residual)}")
        for v in self.residual[:25]:
            lines.append(f"  {v}")
        return "\n".join(lines)


# ──────────────────────────── pure node fix ────────────────────────────


def fix_node_dict(
    node: dict[str, Any],
    *,
    where: str,
    report: Report,
    apply_required_defaults: bool,
    blank_unparseable_schema: bool,
) -> bool:
    """Fix one node's ``config`` in place. Returns True when changed.

    Operates on the already-normalized canonical config (run
    ``normalize_node_config`` first), so it only touches ``node['config']``.
    """
    node_type = str(node.get("node_type") or "TASK")
    node_id = str(node.get("id") or "?")
    label = f"{where}/{node_id}"

    config = dict(node.get("config") or {})
    before = copy.deepcopy(config)

    try:
        spec = get_spec(NodeType(node_type))
    except ValueError:
        spec = get_spec(NodeType.TASK)

    for f in spec.config_fields:
        value = config.get(f.key)
        visible = f.visible_when is None or f.visible_when.matches(config)

        # 1. invalid_enum — stored a Choice label (or wrong case). Remap to
        #    the canonical value. Leave templates and already-valid values.
        if f.choices and not _is_empty(value) and not _is_template(value):
            allowed = {c.value for c in f.choices}
            if value not in allowed and isinstance(value, str):
                canonical = _match_choice(f.choices, value)
                if canonical is not None and canonical != value:
                    config[f.key] = canonical
                    report.bump(f"enum:{node_type}.{f.key}")
                    report.enum_fixes.append(
                        f"{label}  {f.key}: {value!r} → {canonical!r}"
                    )
                    value = canonical

        # 2. missing_required — fill only from REQUIRED_DEFAULTS (opt-in),
        #    otherwise record under "still missing" for a human.
        if f.required and visible and _is_empty(value) and f.default is None:
            default = REQUIRED_DEFAULTS.get((node_type, f.key))
            if default is not None and apply_required_defaults:
                config[f.key] = default
                report.bump(f"default:{node_type}.{f.key}")
                report.default_fixes.append(
                    f"{label}  {f.key} ← {default!r}"
                )
            else:
                report.still_missing.append({"node_id": label, "key": f.key})

        # 3. UNPARSEABLE input_schema — a corrupt JSON string the normalizer
        #    could not parse. Optionally blank it so the node validates;
        #    default is to leave it for manual repair.
        if (
            f.key == "input_schema"
            and isinstance(value, str)
            and value.strip()
            and blank_unparseable_schema
        ):
            config[f.key] = None
            report.bump(f"blank_schema:{node_type}")
            report.schemas_blanked.append(label)

    # Re-validate the fixed config and record anything still broken.
    for v in validate_node(spec, SimpleNamespace(id=label, config=config)):
        if v.severity == "warn":
            continue  # exclusive_group draft state etc. — not an error
        report.residual.append(v.to_dict())

    if config != before:
        node["config"] = config
        report.nodes_changed += 1
        return True
    return False


def _match_choice(choices: tuple, value: str) -> str | None:
    """Return the canonical Choice value for ``value`` (matched against each
    Choice's value then label, case-insensitively), or None."""
    needle = value.strip().casefold()
    for c in choices:
        if c.value.casefold() == needle:
            return c.value
    for c in choices:
        if c.label.casefold() == needle:
            return c.value
    return None


# ──────────────────────────── adapters ────────────────────────────


def fix_workflow_entity(workflow: Any, report: Report, **opts: Any) -> bool:
    changed = False
    for node in workflow.nodes:
        payload = {
            "id": node.id,
            "node_type": (
                node.node_type.value
                if hasattr(node.node_type, "value") else str(node.node_type)
            ),
            "config": node.config,
        }
        if fix_node_dict(payload, where=f"workflow:{workflow.id}",
                         report=report, **opts):
            node.config = payload["config"]
            changed = True
    return changed


def fix_snapshot_definition(definition: dict[str, Any], report: Report,
                            **opts: Any) -> bool:
    changed = False
    for node in definition.get("nodes", []) or []:
        if fix_node_dict(node, where=f"snapshot:{definition.get('id', '?')}",
                         report=report, **opts):
            changed = True
    return changed


# ──────────────────────────── runners ────────────────────────────


def run_workflows(workflow_repo: Any, report: Report, apply: bool,
                  **opts: Any) -> None:
    for workflow in workflow_repo.list():
        report.workflows_seen += 1
        # Dry-run must not mutate the memory repo's aliased entities.
        target = workflow if apply else copy.deepcopy(workflow)
        if not fix_workflow_entity(target, report, **opts):
            continue
        report.workflows_changed += 1
        if apply:
            _save_with_retry(workflow_repo, target, report, opts)


def run_snapshots(run_repo: Any, snapshot_repo: Any, report: Report,
                  apply: bool, **opts: Any) -> None:
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
        if not fix_snapshot_definition(definition, report, **opts):
            continue
        report.snapshots_changed += 1
        if apply:
            snapshot_repo.save(snapshot)


def _iter_runs(run_repo: Any):
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


def _save_with_retry(workflow_repo: Any, workflow: Any, report: Report,
                     opts: dict[str, Any], attempts: int = 3) -> None:
    """CAS-aware save: re-read + re-fix on version conflict."""
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            workflow_repo.save(workflow)
            return
        except Exception as exc:  # repo-specific conflict types
            last_exc = exc
            fresh = workflow_repo.get_by_id(workflow.id)
            if fresh is None:
                raise
            fix_workflow_entity(fresh, Report(), **opts)
            workflow = fresh
    raise last_exc  # type: ignore[misc]


# ──────────────────────────── CLI ────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write changes (default: dry-run report)")
    parser.add_argument("--skip-snapshots", action="store_true",
                        help="fix workflows only")
    parser.add_argument("--apply-required-defaults", action="store_true",
                        help="fill missing-required fields from REQUIRED_DEFAULTS")
    parser.add_argument("--blank-unparseable-schema", action="store_true",
                        help="null out corrupt string input_schema values")
    args = parser.parse_args(argv)

    from app.layer4_frameworks import create_infrastructure
    from app.layer4_frameworks.config.app_config import settings
    from app.layer4_frameworks.persistence import create_repositories

    infra = create_infrastructure(settings)
    repos = create_repositories(settings, getattr(infra, "db", None))
    print(f"backend: {repos.backend_name}  mode: "
          f"{'APPLY' if args.apply else 'dry-run'}")

    opts = dict(
        apply_required_defaults=args.apply_required_defaults,
        blank_unparseable_schema=args.blank_unparseable_schema,
    )
    report = Report()
    run_workflows(repos.workflow, report, args.apply, **opts)
    if not args.skip_snapshots:
        run_snapshots(repos.run, repos.workflow_snapshot, report,
                      args.apply, **opts)

    print(report.summary())
    if not args.apply and report.nodes_changed:
        print("\nDry run — re-run with --apply to write the changes above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

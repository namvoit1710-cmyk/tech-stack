"""Workflow-level node-config validation (NodeSpec, Phase 2).

Thin layer-2 orchestration over the pure ``node_spec.validation``:
resolve each node's spec, collect every violation across all nodes in
one pass (no fail-fast — the FE marks all offending fields at once).

Rollout is governed by the ``NODE_CONFIG_VALIDATION`` env var:

* ``off``     — skip entirely
* ``warn``    — log violations, never block (default; one verification
                pass over real save traffic)
* ``enforce`` — saves with error-severity violations return 400

The flag is a rollout tool inside ONE release (flipped to ``enforce``
at the Phase 6 deploy, then removed) — not a living toggle.

Spec resolution: builtins use the static specs; worker/business nodes
resolve through ``NodeSpecResolver`` when one is supplied (their
``input_binding`` keys check against the registered schema at WARN
severity — never blocking, per design §7).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.node_spec import get_spec
from app.layer1_domain.node_spec.validation import (
    SpecViolation,
    validate_bindings,
    validate_node,
)
from app.layer2_application.definition.node_spec_resolver import NodeSpecResolver

MODE_ENV_VAR = "NODE_CONFIG_VALIDATION"
_VALID_MODES = ("off", "warn", "enforce")


def validation_mode() -> str:
    mode = os.getenv(MODE_ENV_VAR, "warn").strip().lower()
    return mode if mode in _VALID_MODES else "warn"


def validate_workflow_nodes(
    workflow: Workflow,
    spec_resolver: Optional[NodeSpecResolver] = None,
) -> list[SpecViolation]:
    violations: list[SpecViolation] = []
    for node in workflow.nodes:
        spec = (
            spec_resolver.resolve(node) if spec_resolver is not None
            else get_spec(node.node_type)
        )
        violations.extend(validate_node(spec, node))
        violations.extend(validate_bindings(spec, node))
    return violations


def check_workflow_config(
    workflow: Workflow, logger: Any,
    spec_resolver: Optional[NodeSpecResolver] = None,
) -> list[SpecViolation] | None:
    """Validate per the active mode.

    Returns the blocking violations (enforce mode, error severity) or
    ``None`` when the save may proceed.
    """
    mode = validation_mode()
    if mode == "off":
        return None
    violations = validate_workflow_nodes(workflow, spec_resolver)
    if not violations:
        return None
    logger.warning(
        "node config validation violations",
        workflow_id=getattr(workflow, "workflow_id", None),
        mode=mode,
        count=len(violations),
        violations=[v.to_dict() for v in violations],
    )
    if mode != "enforce":
        return None
    errors = [v for v in violations if v.severity == "error"]
    return errors or None

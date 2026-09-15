"""Golden-file guard for the per-node-type AutoForm schemas.

NodeSpec redesign (docs/design/node-spec-single-source.md). The
fixtures under ``tests/fixtures/autoform_golden/`` are the contract of
what the FE receives per node type; the spec renderer must reproduce
them exactly. Intentional diffs are documented in
docs/design/node-spec-fe-changes.md — regenerate the fixture and update
that doc together.

(The legacy ``control_flow_schemas.SCHEMAS`` cross-check was deleted in
Phase 5 along with the module itself.)
"""

import json
import pathlib

import pytest

from app.layer1_domain.node_spec import BUILTIN_SPECS
from app.layer1_domain.node_spec.form_renderer import (
    render_input_schema,
    render_output_schema,
)
from app.layer1_domain.value_objects.node_type import NodeType

FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "autoform_golden"

# Spec'd node types with no palette/catalog form of their own.
_UNLISTED = {NodeType.TASK, NodeType.LOOP_EXIT, NodeType.LOOP_CONTINUE}

CATALOG_TYPES = sorted(
    t.value for t in BUILTIN_SPECS if t not in _UNLISTED
)


def _normalize(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _load(node_type: str) -> str:
    return (FIXTURE_DIR / f"{node_type}.json").read_text()


def test_fixture_set_is_complete() -> None:
    fixture_types = {p.stem for p in FIXTURE_DIR.glob("*.json")}
    assert fixture_types == set(CATALOG_TYPES)


@pytest.mark.parametrize("node_type", CATALOG_TYPES)
def test_rendered_spec_matches_golden(node_type: str) -> None:
    spec = BUILTIN_SPECS[NodeType(node_type)]
    rendered = {
        "input_schema": render_input_schema(spec),
        "output_schema": render_output_schema(spec),
    }
    assert _normalize(rendered) == _load(node_type), (
        f"form_renderer output for {node_type} drifted from its golden "
        "fixture. If the change is intentional, regenerate the fixture and "
        "record the diff in docs/design/node-spec-fe-changes.md."
    )

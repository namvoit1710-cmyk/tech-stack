"""The catalog must NEVER 500 because one node carries a bad kind.

Two bugs this file exists to keep dead — both were live, both were found by hand,
and the full 3500-test suite was green through both:

1. STRINGIFICATION. `default_kind()` returns a real `NodeKind` member, and
   `f"{NodeKind.ACTION}"` renders `"NodeKind.ACTION"` — not `"action"`. So every node
   definition with a blank kind (i.e. every legacy row) got
   `category="My Nodes.NodeKind.ACTION"`, which reached the frontend and produced a
   collection literally named "My Nodes.NodeKind". The enum conversion introduced it;
   no test caught it, because every test passed an explicit string kind.

2. BLAST RADIUS. `NodeDefinitionSummary.kind` is a pydantic `NodeKind` field, so an
   unknown value RAISES — inside catalog assembly. One worker registering `kind="banana"`
   would 500 `/api/v2/node-catalog` and blank the palette for EVERY tenant. A node in the
   wrong bucket is a cosmetic bug; a dead palette is an outage. Never pay for the first
   with the second.
"""
import logging

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer2_application.providers.business_node_provider import _build_summary as business_summary
from app.layer2_application.providers.worker_node_provider import _build_summary as worker_summary

_PORTS = {"in": [{"id": "in"}], "out": [{"id": "out"}]}


class TestNoEnumReprLeaksIntoTheCategory:
    def test_business_blank_kind_heals_to_a_plain_value(self):
        nd = NodeDefinition(tenant_id="t1", name="X", base_worker_id="w")  # blank -> healed to NodeKind
        s = business_summary(nd)
        assert s.category == "My Nodes.action"
        assert "NodeKind" not in s.category, "the enum repr leaked into the category"

    def test_worker_blank_kind_heals_to_a_plain_value(self):
        s = worker_summary({"worker_type": "jira_x", "ports": _PORTS})
        assert s.category == "Jira.action"
        assert "NodeKind" not in s.category


class TestAnUnknownKindNeverKillsTheCatalog:
    def test_worker_junk_kind_is_coerced_and_logged(self, caplog):
        with caplog.at_level(logging.WARNING):
            s = worker_summary({"worker_type": "jira_x", "kind": "banana", "ports": _PORTS})

        assert s.kind == "action"
        assert s.category == "Jira.action", "junk must never reach the FE as 'Jira.banana'"
        assert any("banana" in r.getMessage() for r in caplog.records), (
            "the coercion must be loud, not silent — a silent fallback is how the "
            "Workflow-node-in-the-wrong-bucket bug hid for the life of this branch"
        )

    def test_business_junk_kind_is_coerced_not_raised(self, caplog):
        nd = NodeDefinition(tenant_id="t1", name="X", base_worker_id="w", kind="banana")
        with caplog.at_level(logging.WARNING):
            s = business_summary(nd)   # must NOT raise a pydantic ValidationError

        assert s.kind == "action"
        assert s.category == "My Nodes.action"

    def test_a_retired_kind_on_a_legacy_row_does_not_take_the_palette_down(self):
        """'write' is the kind that was invented in a docstring and never existed.
        A row holding it must render, not 500."""
        nd = NodeDefinition(tenant_id="t1", name="Legacy", base_worker_id="w", kind="write")
        assert business_summary(nd).kind == "action"

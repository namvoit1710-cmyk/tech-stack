"""Tests for BuiltinNodeProvider — the 13 hardcoded builtin node types."""

import pytest

from app.layer1_domain.node_spec import BUILTIN_SPECS
from app.layer1_domain.node_spec.form_renderer import (
    render_input_schema,
    render_output_schema,
)

# Catalog surface (mirrors BuiltinNodeProvider._catalog_specs).
SCHEMAS = {
    spec.node_type.value: {
        "input_schema": render_input_schema(spec),
        "output_schema": render_output_schema(spec),
    }
    for spec in BUILTIN_SPECS.values()
    if spec.node_type.value not in ("TASK", "LOOP_EXIT", "LOOP_CONTINUE")
}
from app.layer2_application.providers import BuiltinNodeProvider
from app.layer2_application.contracts import (
    NodeDefinitionDetail,
    NodeDefinitionSummary,
    NodeRuntime,
    NodeVisibility,
)


@pytest.fixture
def provider() -> BuiltinNodeProvider:
    return BuiltinNodeProvider()


class TestListSummaries:
    def test_returns_one_entry_per_builtin(self, provider):
        summaries = provider.list_summaries()
        assert len(summaries) == len(SCHEMAS)
        assert len(summaries) == 18

    def test_every_summary_is_typed_dto(self, provider):
        for s in provider.list_summaries():
            assert isinstance(s, NodeDefinitionSummary)

    def test_ids_are_builtin_prefixed_and_unique(self, provider):
        ids = [s.id for s in provider.list_summaries()]
        assert all(i.startswith("builtin:") for i in ids)
        assert len(ids) == len(set(ids))

    def test_every_summary_has_visible_visibility(self, provider):
        for s in provider.list_summaries():
            assert s.visibility == NodeVisibility.VISIBLE

    def test_every_summary_has_spec_version_1_0_0(self, provider):
        for s in provider.list_summaries():
            assert s.spec_version == "1.0.0"

    def test_runtimes_are_engine_or_pending(self, provider):
        runtimes = {s.runtime for s in provider.list_summaries()}
        assert NodeRuntime.ENGINE in runtimes
        assert NodeRuntime.PENDING in runtimes
        # No builtin should surface as worker runtime.
        assert NodeRuntime.WORKER not in runtimes

    def test_condition_classified_as_engine(self, provider):
        s = next(s for s in provider.list_summaries() if s.id == "builtin:condition")
        assert s.runtime == NodeRuntime.ENGINE
        assert s.node_type == "CONDITION"

    def test_input_classified_as_pending(self, provider):
        s = next(s for s in provider.list_summaries() if s.id == "builtin:input")
        assert s.runtime == NodeRuntime.PENDING
        assert s.node_type == "INPUT"


class TestGetDetail:
    def test_condition_detail_has_two_output_ports(self, provider):
        d = provider.get_detail("builtin:condition")
        out_ids = {p.id for p in d.ports.outputs}
        assert "true_branch" in out_ids
        assert "false_branch" in out_ids

    def test_detail_schemas_parse_as_form_fields(self, provider):
        d = provider.get_detail("builtin:condition")
        assert isinstance(d, NodeDefinitionDetail)
        assert len(d.input_schema) > 0
        assert len(d.output_schema) > 0
        # Each element must be a FormField DTO (pydantic model).
        assert all(hasattr(f, "key") and hasattr(f, "output_type") for f in d.input_schema)

    def test_unknown_id_returns_none(self, provider):
        assert provider.get_detail("builtin:nonexistent") is None

    def test_non_builtin_prefix_returns_none(self, provider):
        assert provider.get_detail("worker:anything") is None
        assert provider.get_detail("business:anything") is None

    def test_wrong_spec_version_raises(self, provider):
        with pytest.raises(ValueError, match="spec_version"):
            provider.get_detail("builtin:condition", spec_version="2.0.0")

    def test_spec_version_1_0_0_passes(self, provider):
        d = provider.get_detail("builtin:condition", spec_version="1.0.0")
        assert d is not None


class TestBuiltinRoundTripWithContracts:
    """Every builtin detail must validate end-to-end through FormField.
    This is a sharper tripwire than the round-trip test on raw SCHEMAS."""

    @pytest.mark.parametrize("node_type_str", list(SCHEMAS.keys()))
    def test_detail_builds_without_validation_error(self, provider, node_type_str):
        d = provider.get_detail(f"builtin:{node_type_str.lower()}")
        assert d is not None
        # Serializable to camelCase JSON (FE wire format).
        dumped = d.model_dump(by_alias=True)
        assert dumped["provider"] == "builtin"
        assert dumped["specVersion"] == "1.0.0"

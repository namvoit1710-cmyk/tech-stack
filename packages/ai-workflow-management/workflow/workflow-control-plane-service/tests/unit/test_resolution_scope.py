"""Unit tests for the ResolutionScope value object.

Covers each frame's ``try_resolve`` contract in isolation
(Strategy / Chain-of-Responsibility), the ``ResolutionScope``
composite's frame-walking ordering and immutability, the
``ResolutionScopeBuilder`` canonical frame order, and a
future-frame demo proving that adding a new scope source
type (``$tenant``) is one frame class plus one builder line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, RunContext, WorkflowRun
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.resolution_scope import (
    UNRESOLVED,
    IterationLocalsFrame,
    ParentRunFrame,
    ResolutionScope,
    ResolutionScopeBuilder,
    RunLocalsFrame,
    SystemCredentialsFrame,
    SystemRunFrame,
    SystemVariablesFrame,
    SystemWorkflowFrame,
    UserVariablesFrame,
    get_current_scope,
    reset_current_scope,
    set_current_scope,
)


# ─── Frame-level contract tests (Strategy in isolation) ──────────────


class TestIterationLocalsFrame:
    def test_resolves_local_key(self):
        f = IterationLocalsFrame(locals_={"loop": {"index": 3}})
        assert f.try_resolve("loop") == {"index": 3}

    def test_unresolved_for_unknown_key(self):
        f = IterationLocalsFrame(locals_={"loop": {}})
        assert f.try_resolve("input") is UNRESOLVED

    def test_known_sources_lists_keys(self):
        f = IterationLocalsFrame(locals_={"loop": {}, "_action": "x"})
        assert sorted(f.known_sources()) == ["_action", "loop"]

    def test_empty_frame_is_unresolved_everywhere(self):
        f = IterationLocalsFrame()
        assert f.try_resolve("anything") is UNRESOLVED
        assert f.known_sources() == []


class TestRunLocalsFrame:
    def test_resolves_node_output(self):
        f = RunLocalsFrame(run_locals={"node_a": {"x": 1}})
        assert f.try_resolve("node_a") == {"x": 1}

    def test_unresolved_for_missing_node(self):
        f = RunLocalsFrame(run_locals={"node_a": {}})
        assert f.try_resolve("node_z") is UNRESOLVED

    def test_known_sources_returns_node_ids(self):
        f = RunLocalsFrame(run_locals={"input": {}, "node_a": {}})
        assert sorted(f.known_sources()) == ["input", "node_a"]


class TestUserVariablesFrame:
    async def test_variable_source_returns_effective_dict(self):
        f = UserVariablesFrame(effective={"tenant_id": "acme"})
        assert await f.try_resolve("variable") == {"tenant_id": "acme"}

    async def test_other_sources_unresolved(self):
        f = UserVariablesFrame(effective={"x": 1})
        assert await f.try_resolve("input") is UNRESOLVED
        assert await f.try_resolve("parent") is UNRESOLVED

    def test_known_sources_constant(self):
        # $variable is the single reserved source — always advertised
        # whether or not the effective dict has any keys, so authors
        # can discover it from the resolver's "available sources" hint.
        f = UserVariablesFrame(effective={})
        assert f.known_sources() == ["variable"]


class TestSystemRunFrame:
    def test_resolves_run_source(self):
        f = SystemRunFrame(snapshot={"id": "r1", "status": "RUNNING"})
        assert f.try_resolve("run") == {"id": "r1", "status": "RUNNING"}

    def test_unresolved_for_other_sources(self):
        f = SystemRunFrame(snapshot={"id": "r1"})
        assert f.try_resolve("workflow") is UNRESOLVED
        assert f.try_resolve("variables") is UNRESOLVED
        assert f.try_resolve("variable") is UNRESOLVED

    def test_known_sources_constant(self):
        assert SystemRunFrame().known_sources() == ["run"]


class TestSystemWorkflowFrame:
    def test_resolves_workflow_source(self):
        f = SystemWorkflowFrame(snapshot={"id": "wf1", "name": "Test"})
        assert f.try_resolve("workflow") == {"id": "wf1", "name": "Test"}

    def test_unresolved_for_other_sources(self):
        f = SystemWorkflowFrame(snapshot={"id": "wf1"})
        assert f.try_resolve("run") is UNRESOLVED
        assert f.try_resolve("variables") is UNRESOLVED

    def test_known_sources_constant(self):
        assert SystemWorkflowFrame().known_sources() == ["workflow"]


class TestSystemVariablesFrame:
    def test_resolves_variables_source(self):
        f = SystemVariablesFrame(vars_view={"userCount": 42, "label": "active"})
        assert f.try_resolve("variables") == {"userCount": 42, "label": "active"}

    def test_unresolved_for_singular_variable(self):
        # ``$variable`` (singular) is the user-defined namespace; ``$variables``
        # (plural) is the runtime mutable namespace. They must not collide.
        f = SystemVariablesFrame(vars_view={"x": 1})
        assert f.try_resolve("variable") is UNRESOLVED

    def test_unresolved_for_other_sources(self):
        f = SystemVariablesFrame()
        assert f.try_resolve("run") is UNRESOLVED
        assert f.try_resolve("workflow") is UNRESOLVED

    def test_known_sources_constant(self):
        assert SystemVariablesFrame().known_sources() == ["variables"]

    def test_empty_view_still_resolves(self):
        # Picker shows an empty Variables panel as a discoverability hint
        # — the frame must still claim the ``variables`` source even when
        # nothing has been set yet.
        f = SystemVariablesFrame(vars_view={})
        assert f.try_resolve("variables") == {}

    def test_returns_raw_file_ref_for_downstream_traversal(self):
        # No-``extracted`` OData model: the frame returns the raw ``__vars``
        # file_ref pointer. Unwrapping/traversal (``$variables.X`` and the
        # empty-path ``$variables``) is done downstream by the async strategy
        # chain, not by the frame. So the frame is a pure pass-through here.
        wrapped = {
            "__file_ref": True,
            "file_id": "abc.csv",
            "row_count": 1,
            "columns": ["userCount", "label"],
            "extracted": {
                "userCount": {"value": 42},
                "label": {"value": "active"},
            },
        }
        f = SystemVariablesFrame(vars_view=wrapped)
        assert f.try_resolve("variables") == wrapped

    def test_plain_dict_passes_through(self):
        # When the projector hasn't run yet the slot is a plain dict.
        f = SystemVariablesFrame(vars_view={"x": 1, "y": "two"})
        assert f.try_resolve("variables") == {"x": 1, "y": "two"}


class TestParentRunFrame:
    def test_no_parent_returns_unresolved(self):
        f = ParentRunFrame(parent_scope=None)
        assert f.try_resolve("parent") is UNRESOLVED
        assert f.try_resolve("root") is UNRESOLVED

    def test_parent_scope_returns_flat_view(self):
        parent = ResolutionScope(frames=(
            RunLocalsFrame(run_locals={"node_a": "parent_value"}),
        ))
        f = ParentRunFrame(parent_scope=parent)
        assert f.try_resolve("parent") == {"node_a": "parent_value"}

    def test_root_walks_to_topmost_ancestor(self):
        # grand → parent → child chain. $root from child must reach grand.
        grand = ResolutionScope(frames=(
            RunLocalsFrame(run_locals={"top_node": "GRAND"}),
        ))
        parent = ResolutionScope(frames=(
            RunLocalsFrame(run_locals={"mid_node": "PARENT"}),
            ParentRunFrame(parent_scope=grand),
        ))
        # The "child" frame is what we're testing — it carries `parent`
        # as its ParentRunFrame target. Calling try_resolve("root") on
        # that ParentRunFrame walks up until no further ancestor is
        # reachable.
        child_frame = ParentRunFrame(parent_scope=parent)
        assert child_frame.try_resolve("root") == {"top_node": "GRAND"}

    def test_known_sources_only_when_parent_present(self):
        assert ParentRunFrame(parent_scope=None).known_sources() == []
        scope = ResolutionScope(frames=(RunLocalsFrame(run_locals={"x": 1}),))
        assert ParentRunFrame(parent_scope=scope).known_sources() == ["parent", "root"]


# ─── ResolutionScope composite tests ─────────────────────────────────


class TestResolutionScopeLookup:
    async def test_walks_frames_in_order_first_hit_wins(self):
        scope = ResolutionScope(frames=(
            IterationLocalsFrame(locals_={"loop": "ITERATION"}),
            RunLocalsFrame(run_locals={"loop": "RUN"}),  # would also match
        ))
        # Innermost frame wins — IterationLocalsFrame's "loop" beats
        # RunLocalsFrame's "loop" entry.
        assert await scope.lookup("loop") == "ITERATION"

    async def test_returns_unresolved_when_no_frame_claims(self):
        scope = ResolutionScope(frames=(
            RunLocalsFrame(run_locals={"node_a": {}}),
        ))
        assert await scope.lookup("nonexistent") is UNRESOLVED

    def test_known_sources_unions_across_frames(self):
        scope = ResolutionScope(frames=(
            IterationLocalsFrame(locals_={"loop": {}}),
            RunLocalsFrame(run_locals={"input": {}, "node_a": {}}),
            UserVariablesFrame(effective={}),
        ))
        assert scope.known_sources() == ["input", "loop", "node_a", "variable"]


class TestResolutionScopeImmutability:
    async def test_with_replaced_frame_returns_new_instance(self):
        original = ResolutionScope(frames=(
            IterationLocalsFrame(locals_={"loop": {"index": 0}}),
            RunLocalsFrame(run_locals={"input": {}}),
        ))
        replacement = IterationLocalsFrame(locals_={"loop": {"index": 5}})
        new = original.with_replaced_frame(IterationLocalsFrame, replacement)

        # Original is untouched — concurrent body tasks can each call
        # with_replaced_frame on the SAME parent scope without polluting
        # each other's iteration state.
        assert await original.lookup("loop") == {"index": 0}
        assert await new.lookup("loop") == {"index": 5}
        assert original is not new

    async def test_with_replaced_frame_only_swaps_first_match(self):
        # Defensive: if a chain ever holds two frames of the same type,
        # only the first is replaced.
        scope = ResolutionScope(frames=(
            IterationLocalsFrame(locals_={"loop": "FIRST"}),
            IterationLocalsFrame(locals_={"loop": "SECOND"}),
        ))
        new = scope.with_replaced_frame(
            IterationLocalsFrame,
            IterationLocalsFrame(locals_={"loop": "REPLACED"}),
        )
        # Lookup still hits the FIRST frame — but it's the replacement now.
        assert await new.lookup("loop") == "REPLACED"


class TestResolutionScopeFlatView:
    def test_merges_run_and_iteration_innermost_wins(self):
        scope = ResolutionScope(frames=(
            IterationLocalsFrame(locals_={"loop": "ITER"}),
            RunLocalsFrame(run_locals={"loop": "RUN", "node_a": "X"}),
        ))
        view = scope.flat_view()
        # Iteration frame is innermost so its "loop" wins.
        assert view == {"loop": "ITER", "node_a": "X"}

    def test_skips_user_variables_and_parent_frames(self):
        # $variable / $parent / $root are reserved sources — they must
        # NOT spill into the flat view, otherwise a node id named
        # "variable" would shadow the reserved source.
        scope = ResolutionScope(frames=(
            RunLocalsFrame(run_locals={"node_a": "X"}),
            UserVariablesFrame(effective={"tenant_id": "acme"}),
        ))
        view = scope.flat_view()
        assert view == {"node_a": "X"}


# ─── ResolutionScopeBuilder canonical-order tests ────────────────────


def _make_run(run_id="run-1", *, parent_run_id=None, root_run_id="",
              variables=None, user_vars=None):
    return WorkflowRun(
        id=run_id,
        workflow_id="wf-1",
        parent_run_id=parent_run_id,
        root_run_id=root_run_id,
        context=RunContext(variables=dict(variables or {})),
        user_variables=dict(user_vars or {}),
    )


def _make_workflow(workflow_id="wf-1", *, variables=None):
    return Workflow(
        id=workflow_id, name="Test",
        variables=dict(variables or {}),
    )


class TestResolutionScopeBuilder:
    def test_canonical_frame_order(self):
        builder = ResolutionScopeBuilder()
        scope = builder.build(
            _make_run(variables={"node_a": "A"}),
            workflow=_make_workflow(variables={"endpoint": "default"}),
        )
        types = [type(f).__name__ for f in scope.frames]
        # Innermost-first order is what makes per-iteration $loop win
        # over a node named "loop", and what makes $variable resolve
        # before parent fallback. SystemRun/Workflow/Variables frames
        # sit between user variables and parent fallback so authors
        # can't shadow $run / $workflow / $variables with run-locals.
        assert types == [
            "IterationLocalsFrame",
            "RunLocalsFrame",
            "UserVariablesFrame",
            "SystemRunFrame",
            "SystemWorkflowFrame",
            "SystemVariablesFrame",
            "SystemCredentialsFrame",
            "ParentRunFrame",
        ]

    async def test_system_run_frame_resolves_run_id(self):
        builder = ResolutionScopeBuilder()
        run = _make_run()
        scope = builder.build(run, workflow=_make_workflow())
        snapshot = await scope.lookup("run")
        assert snapshot is not UNRESOLVED
        assert snapshot["id"] == run.id
        assert snapshot["workflow_id"] == run.workflow_id

    async def test_system_workflow_frame_resolves_workflow_name(self):
        builder = ResolutionScopeBuilder()
        wf = _make_workflow()
        scope = builder.build(_make_run(), workflow=wf)
        snapshot = await scope.lookup("workflow")
        assert snapshot is not UNRESOLVED
        assert snapshot["id"] == wf.id
        assert snapshot["name"] == "Test"

    async def test_system_variables_frame_reads_underscore_vars_slot(self):
        builder = ResolutionScopeBuilder()
        run = _make_run(variables={"__vars": {"userCount": 42}})
        scope = builder.build(run, workflow=_make_workflow())
        assert await scope.lookup("variables") == {"userCount": 42}

    async def test_system_variables_frame_empty_when_slot_missing(self):
        builder = ResolutionScopeBuilder()
        run = _make_run(variables={})
        scope = builder.build(run, workflow=_make_workflow())
        assert await scope.lookup("variables") == {}

    async def test_user_variables_defaults_overridden_by_run_user_variables(self):
        builder = ResolutionScopeBuilder()
        run = _make_run(user_vars={"endpoint": "https://prod"})
        wf = _make_workflow(variables={"endpoint": "https://default", "scope_id": 7})
        scope = builder.build(run, workflow=wf)
        assert await scope.lookup("variable") == {
            "endpoint": "https://prod",   # run override wins
            "scope_id": 7,                # workflow default kept
        }

    async def test_root_user_variables_param_takes_precedence_over_run(self):
        # Sub-workflow case: engine resolves the ROOT run's user_variables
        # and passes them via root_user_variables. The current run's
        # user_variables are ignored — they belong to a child run row
        # which has no authoritative override (per ADR: only root carries).
        builder = ResolutionScopeBuilder()
        run = _make_run(user_vars={"stale": "ignore_me"})
        wf = _make_workflow(variables={"endpoint": "default"})
        scope = builder.build(
            run, workflow=wf,
            root_user_variables={"endpoint": "from_root", "tenant": "acme"},
        )
        assert await scope.lookup("variable") == {
            "endpoint": "from_root",  # root override wins
            "tenant": "acme",         # root-only override visible
        }
        assert "stale" not in await scope.lookup("variable")

    def test_identity_fields_populated(self):
        builder = ResolutionScopeBuilder()
        run = _make_run(run_id="child-1", parent_run_id="root-1", root_run_id="root-1")
        run.depth_level = 2
        scope = builder.build(run, workflow=_make_workflow())
        assert scope.run_id == "child-1"
        assert scope.root_run_id == "root-1"
        assert scope.parent_run_id == "root-1"
        assert scope.depth == 2

    async def test_iteration_locals_threaded_into_frame(self):
        builder = ResolutionScopeBuilder()
        scope = builder.build(
            _make_run(),
            workflow=_make_workflow(),
            iteration_locals={"loop": {"index": 7}},
        )
        assert await scope.lookup("loop") == {"index": 7}

    async def test_parent_scope_threaded_for_dollar_parent(self):
        builder = ResolutionScopeBuilder()
        parent_scope = ResolutionScope(frames=(
            RunLocalsFrame(run_locals={"parent_node": "P"}),
        ))
        scope = builder.build(
            _make_run(),
            workflow=_make_workflow(),
            parent_scope=parent_scope,
        )
        assert await scope.lookup("parent") == {"parent_node": "P"}


# ─── Ambient contextvar tests ────────────────────────────────────────


class TestAmbientScopeContextVar:
    def test_set_and_get_round_trip(self):
        scope = ResolutionScope(frames=(RunLocalsFrame(run_locals={"x": 1}),))
        token = set_current_scope(scope)
        try:
            assert get_current_scope() is scope
        finally:
            reset_current_scope(token)
        assert get_current_scope() is None

    def test_reset_restores_previous_scope(self):
        outer = ResolutionScope(frames=(RunLocalsFrame(run_locals={"x": "outer"}),))
        inner = ResolutionScope(frames=(RunLocalsFrame(run_locals={"x": "inner"}),))
        outer_token = set_current_scope(outer)
        try:
            inner_token = set_current_scope(inner)
            try:
                assert get_current_scope() is inner
            finally:
                reset_current_scope(inner_token)
            assert get_current_scope() is outer
        finally:
            reset_current_scope(outer_token)


# ─── Future-frame demo: extensibility proof ──────────────────────────


@dataclass(frozen=True)
class _TenantFrame:
    """Stub frame demonstrating the design's extensibility goal.

    Adding a new ``$tenant.X`` source type takes one new class
    + one builder line. Existing frames, the resolver, the strategy
    chain, and every node handler stay unchanged.
    """
    tenant_config: Mapping[str, Any] = field(default_factory=dict)
    name: str = "tenant"

    def try_resolve(self, source: str) -> Any:
        if source == "tenant":
            return self.tenant_config
        return UNRESOLVED

    def known_sources(self) -> list[str]:
        return ["tenant"]


class TestFutureFrameDemo:
    async def test_tenant_frame_resolves_in_chain(self):
        scope = ResolutionScope(frames=(
            IterationLocalsFrame(locals_={}),
            RunLocalsFrame(run_locals={"node_a": "X"}),
            _TenantFrame(tenant_config={"id": "acme", "tier": "enterprise"}),
        ))
        # `$tenant` resolves through the new frame; everything else
        # resolves through the existing frames unchanged.
        assert await scope.lookup("tenant") == {"id": "acme", "tier": "enterprise"}
        assert await scope.lookup("node_a") == "X"
        assert await scope.lookup("nonexistent") is UNRESOLVED

    def test_tenant_frame_does_not_break_known_sources(self):
        scope = ResolutionScope(frames=(
            RunLocalsFrame(run_locals={"input": {}}),
            UserVariablesFrame(effective={}),
            _TenantFrame(tenant_config={}),
        ))
        # The resolver's "available sources" diagnostic enumerates every
        # frame's known_sources — proving the new frame integrates with
        # error reporting too.
        assert "tenant" in scope.known_sources()
        assert "variable" in scope.known_sources()
        assert "input" in scope.known_sources()


# ─── INPUT-node contributions to $variable ───────────────────────────


def _make_input_node(node_id: str) -> WorkflowNode:
    return WorkflowNode(id=node_id, name=node_id, node_type=NodeType.INPUT)


def _file_ref(extracted: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal file_ref-shaped dict that matches what the
    INPUT handler writes after submission. Only ``__file_ref`` and
    ``extracted`` matter for the scalar-flattening contract."""
    return {
        "__file_ref": True,
        "file_id": "f-test",
        "extracted": {k: {"value": v} for k, v in extracted.items()},
    }


class TestBuilderInputContributionsLayer:
    """``input_contributions`` is the 3rd / freshest layer in
    ``$variable``. Layer precedence is defaults < overrides < inputs."""

    async def test_inputs_override_defaults(self):
        builder = ResolutionScopeBuilder()
        run = _make_run()
        wf = _make_workflow(variables={"endpoint": "https://default"})
        scope = builder.build(
            run, workflow=wf,
            input_contributions={"endpoint": "https://from-input"},
        )
        assert await scope.lookup("variable") == {"endpoint": "https://from-input"}

    async def test_inputs_override_user_variables(self):
        builder = ResolutionScopeBuilder()
        run = _make_run(user_vars={"endpoint": "https://from-user-vars"})
        wf = _make_workflow(variables={"endpoint": "https://default"})
        scope = builder.build(
            run, workflow=wf,
            input_contributions={"endpoint": "https://from-input"},
        )
        assert await scope.lookup("variable") == {"endpoint": "https://from-input"}

    async def test_inputs_merge_with_other_layers(self):
        # Each layer contributes a distinct key — all three should appear.
        builder = ResolutionScopeBuilder()
        run = _make_run(user_vars={"override_only": 2})
        wf = _make_workflow(variables={"default_only": 1})
        scope = builder.build(
            run, workflow=wf,
            input_contributions={"input_only": 3},
        )
        assert await scope.lookup("variable") == {
            "default_only": 1,
            "override_only": 2,
            "input_only": 3,
        }

    async def test_empty_inputs_does_not_clobber_other_layers(self):
        builder = ResolutionScopeBuilder()
        run = _make_run(user_vars={"x": "user"})
        wf = _make_workflow(variables={"x": "default"})
        # Empty / None input_contributions means: no INPUT submissions
        # this run, so the other layers stand.
        scope = builder.build(run, workflow=wf, input_contributions={})
        assert await scope.lookup("variable") == {"x": "user"}
        scope = builder.build(run, workflow=wf, input_contributions=None)
        assert await scope.lookup("variable") == {"x": "user"}

    async def test_root_user_variables_path_still_layers_inputs_on_top(self):
        # Sub-workflow case: root layer is passed via root_user_variables;
        # this run's INPUT contributions still merge on top.
        builder = ResolutionScopeBuilder()
        run = _make_run(user_vars={"ignored": "stale"})
        wf = _make_workflow(variables={"k": "default"})
        scope = builder.build(
            run, workflow=wf,
            root_user_variables={"k": "from-root", "tenant": "acme"},
            input_contributions={"k": "from-this-run-input"},
        )
        # k: child INPUT > root layer > defaults
        # tenant: only in root layer
        assert await scope.lookup("variable") == {
            "k": "from-this-run-input",
            "tenant": "acme",
        }


class TestBuilderLazyInputFetch:
    """Lazy (production) path: with no eager ``input_contributions`` and a
    ``materialize`` callable wired, ``UserVariablesFrame`` fetches INPUT-node
    scalar contributions on demand for ``$variable.X`` — the no-``extracted``
    OData model. Mirrors what ``RunScopeManager`` does in production."""

    def _wf_with_input(self, node_id="input_a", *, variables=None):
        return Workflow(
            id="wf-1", name="Test",
            variables=dict(variables or {}),
            nodes=[_make_input_node(node_id)],
            edges=[],
        )

    async def test_lazy_fetch_flattens_scalars_only(self):
        materialized = {
            "username": "Alice",
            "_internal": "skip",    # underscore-prefixed → dropped
            "nested": {"x": 1},     # nested dict → dropped (scalars only)
            "tags": ["a", "b"],     # list → dropped
            "count": 7,
        }

        async def materialize(_ref):
            return materialized

        builder = ResolutionScopeBuilder(materialize=materialize)
        run = _make_run(variables={"input_a": _file_ref({"username": "Alice"})})
        wf = self._wf_with_input("input_a", variables={"default_only": "d"})
        scope = builder.build(run, workflow=wf)  # no input_contributions → lazy
        assert await scope.lookup("variable") == {
            "default_only": "d",
            "username": "Alice",
            "count": 7,
        }

    async def test_lazy_fetch_disabled_without_materialize(self):
        # No materialize wired → fetch=None → INPUT contributions are not
        # fetched; only the in-memory defaults/overrides resolve.
        builder = ResolutionScopeBuilder()  # materialize=None
        run = _make_run(variables={"input_a": _file_ref({"username": "Alice"})})
        wf = self._wf_with_input("input_a", variables={"default_only": "d"})
        scope = builder.build(run, workflow=wf)
        assert await scope.lookup("variable") == {"default_only": "d"}

    async def test_inherited_input_refs_layer_below_own(self):
        # Root (inherited) INPUT refs fetched first, this-run's own after →
        # own wins on key collision.
        async def materialize(ref):
            return {"k": ref.get("file_id")}  # value distinguishes the refs

        builder = ResolutionScopeBuilder(materialize=materialize)
        own_ref = _file_ref({})
        own_ref["file_id"] = "OWN"
        run = _make_run(variables={"input_own": own_ref})
        wf = self._wf_with_input("input_own")
        root_ref = _file_ref({})
        root_ref["file_id"] = "ROOT"
        scope = builder.build(
            run, workflow=wf,
            inherited_input_refs=(("root_input", root_ref),),
        )
        assert await scope.lookup("variable") == {"k": "OWN"}

    async def test_memo_fetches_once_per_pass(self):
        calls = []

        async def materialize(_ref):
            calls.append(1)
            return {"username": "Alice"}

        builder = ResolutionScopeBuilder(materialize=materialize)
        run = _make_run(variables={"input_a": _file_ref({})})
        wf = self._wf_with_input("input_a")
        scope = builder.build(run, workflow=wf)
        await scope.lookup("variable")
        await scope.lookup("variable")
        # Per-pass memo: the single INPUT ref is materialized exactly once
        # even though ``$variable`` was resolved twice.
        assert len(calls) == 1

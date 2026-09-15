"""End-to-end resolution through the REAL resolver + file service for the
no-``extracted`` OData model.

The scope/builder unit tests use ``fetch=None`` / stub callables and so cannot
exercise the actual ``lookup → file_ref → strategy chain → materialize`` path.
This module wires the real ``DataStoreService`` + ``MockFileServiceClient`` +
``FileBackedResolverStrategy`` and resolves through ``VariableResolver`` against
a ``ResolutionScope``, with ``__vars`` / INPUT outputs **wire-thinned**
(``extracted`` stripped) to force on-demand fetch — i.e. production shape.
"""
from __future__ import annotations

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.resolution_scope import ResolutionScopeBuilder
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


def _resolver(data_store, file_client) -> VariableResolver:
    return VariableResolver([
        MetadataResolverStrategy(),
        TraversalResolverStrategy(),
        FileBackedResolverStrategy(data_store, file_client=file_client),
    ])


async def _setup():
    client = MockFileServiceClient()
    data_store = DataStoreService(client, CsvConverter())

    # Store __vars and an INPUT submission as CSV-backed file_refs. Under the
    # no-``extracted`` model ``store_data`` already returns pointer-only refs,
    # so resolution must fetch on demand — no wire-thinning needed.
    vars_ref = await data_store.store_data("__vars", {"k": "v", "n": 5})
    input_ref = await data_store.store_data("input_a", {"inputKey": "hello"})

    workflow = Workflow(
        id="wf-1", name="wf",
        nodes=[WorkflowNode(id="input_a", name="input_a", node_type=NodeType.INPUT)],
        edges=[],
    )
    run = WorkflowRun(
        id="run-1", workflow_id="wf-1", root_run_id="run-1",
        context=RunContext(variables={"__vars": vars_ref, "input_a": input_ref}),
    )
    builder = ResolutionScopeBuilder(materialize=data_store.materialize)
    scope = builder.build(run, workflow=workflow)
    return _resolver(data_store, client), scope


async def test_variables_path_resolves_via_strategy_chain():
    # $variables.k: SystemVariablesFrame returns the raw __vars file_ref;
    # the async strategy chain materializes it on demand (no extracted).
    resolver, scope = await _setup()
    assert await resolver.resolve("{{$variables.k}}", scope) == "v"
    assert await resolver.resolve("{{$variables.n}}", scope) == 5


async def test_variable_input_contribution_resolves_via_lazy_fetch():
    # $variable.inputKey: UserVariablesFrame lazily materializes the INPUT
    # node's file_ref and exposes its top-level scalars.
    resolver, scope = await _setup()
    assert await resolver.resolve("{{$variable.inputKey}}", scope) == "hello"


async def test_bare_variables_returns_file_ref_pointer():
    # KNOWN BEHAVIOUR CHANGE (Stage 3a): bare ``$variables`` (empty path) now
    # returns the raw ``__vars`` file_ref pointer — consistent with bare
    # ``$node`` — because ``_resolve_single`` short-circuits empty paths
    # (variable_resolver.py:434 ``return source_data``). Pre-3a it returned an
    # unwrapped flat dict. Path access (``$variables.k``) still materializes.
    resolver, scope = await _setup()
    result = await resolver.resolve("{{$variables}}", scope)
    assert isinstance(result, dict) and result.get("__file_ref") is True

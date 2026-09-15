"""Design-time autocomplete for ``{{$...}}`` expressions.

Workflow-scoped sibling of the run-scoped variable-suggestions feature: no
run is required, suggestions are derived purely from the workflow document
(node output schemas, ``input_schema``, SET_VARIABLE declarations). The
same scope-provider list is reused with ``run=None`` / ``variables={}`` —
runtime extras (samples, file columns/preview rows, child-run scopes,
``$artifacts`` entries) simply come back empty.

Runtime-only sources are intentionally absent at design time:

- ``$parent`` / ``$root`` — parent linkage exists only on run aggregates
- ``$loop`` — loop-body membership isn't derivable from topology alone
- child-run node scopes / child artifacts — guarded out by ``run=None``

The endpoint accepts the partial expression as typed (``q``) and returns a
flat ranked ``matches[]`` list (see :mod:`.expression_query` for the
parse/match/rank semantics) plus per-scope summary counters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.get_design_time_variable_suggestions.use_cases.expression_query import (
    PathMatch,
    parse_query,
    match,
    source_token,
)
from app.layer2_application.features.get_variable_suggestions.use_cases.get_variable_suggestions_use_case import (
    Scope,
    _collect_upstream_node_ids,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.environment_repo_interface import (
    ListEnvironmentsFilter,
)
from app.layer2_application.repositories.workflow_repository_interface import (
    IWorkflowRepository,
)

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


# ---------------------------------------------------------------------------
# Command / Result types
# ---------------------------------------------------------------------------

@dataclass
class GetDesignTimeVariableSuggestionsCommand:
    workflow_id: str = ""
    node_id: str = ""       # the target node being configured
    q: str = ""             # partial expression as typed; "" = everything
    limit: int = DEFAULT_LIMIT
    environment_id: str = ""  # SA-2365: which environment's $env vars to offer; "" = none


@dataclass
class SuggestionMatch:
    """One autocomplete entry. Field names mirror ``VariablePath`` so the FE
    can share types with the run-scoped endpoint."""

    scope_type: str = "node"
    scope_id: str = ""
    scope_label: str = ""
    node_type: str | None = None
    expression_prefix: str = ""
    path: str = ""
    kind: str = "scalar"
    output_type: str = "string"
    item_type: str | None = None
    label: str = ""
    display_path: str = ""
    match_kind: str = "all"     # all | source | prefix | substring


@dataclass
class ScopeSummary:
    scope_type: str = "node"
    scope_id: str = ""
    label: str = ""
    expression_prefix: str = ""
    match_count: int = 0
    returned_count: int = 0
    truncated: bool = False


@dataclass
class GetDesignTimeVariableSuggestionsResult:
    workflow_id: str = ""
    node_id: str = ""
    query: str = ""
    matches: list[SuggestionMatch] = field(default_factory=list)
    scopes: list[ScopeSummary] = field(default_factory=list)
    total_matches: int = 0
    truncated: bool = False
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Use case
# ---------------------------------------------------------------------------

class GetDesignTimeVariableSuggestionsUseCase:
    """Load workflow, build scopes via the shared providers (run=None),
    filter/rank against the partial expression."""

    def __init__(
        self,
        logger: ILogger,
        workflow_repo: IWorkflowRepository,
        scope_providers: Optional[list[Any]] = None,
        environment_repo: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo
        self._env_repo = environment_repo
        self._providers = sorted(
            scope_providers or [], key=lambda p: getattr(p, "order", 0),
        )

    async def execute(
        self, command: GetDesignTimeVariableSuggestionsCommand,
    ) -> GetDesignTimeVariableSuggestionsResult:
        workflow = await self.workflow_repo.aget_by_id(command.workflow_id)
        if not workflow:
            return GetDesignTimeVariableSuggestionsResult(
                workflow_id=command.workflow_id, node_id=command.node_id,
                query=command.q,
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": command.workflow_id},
            )

        target_node = workflow.get_node(command.node_id)
        if target_node:
            upstream_node_ids = _collect_upstream_node_ids(workflow, command.node_id)
        else:
            # Target node isn't persisted yet — expose every node so the FE
            # can filter. (Parity with the run-scoped endpoint.)
            upstream_node_ids = sorted(n.id for n in workflow.nodes)

        # SA-2365 (Hướng C): the design-time Environment ($env) panel offers the
        # UNION of variable KEYS across ALL environments — keys only, NO values.
        # Environments in a section share the same keys; the concrete value
        # differs per environment and is resolved at run (env chosen in the
        # Execute popup) — to preview values, open the Environments config page.
        # Independent of any selected environment, so the panel always lets you
        # drag `{{$env.KEY}}` into a node field while authoring.
        variables: dict[str, Any] = {}
        if self._env_repo is not None:
            wf_tenant = getattr(workflow, "tenant_id", None) or "system"
            try:
                page = self._env_repo.list_paged(
                    ListEnvironmentsFilter(tenant_id=wf_tenant, include_inactive=False, top=500)
                )
                envs = list(page.items)
            except Exception:  # noqa: BLE001 — suggestions must never fail the panel
                envs = []
            # Values stay EMPTY (previewed on the Environments config page). A
            # future feature keeps every environment's key set identical, so the
            # union is unambiguous and no per-key section label is needed.
            env_keys: dict[str, Any] = {}
            for env in envs:
                for key in env.suggestion_env_map().keys():
                    env_keys.setdefault(key, "")
            if env_keys:
                variables = {"env": env_keys}

        scopes: list[Scope] = []
        for provider in self._providers:
            produced = await provider.build(
                run=None,
                target_node_id=command.node_id,
                target_workflow=workflow,
                variables=variables,
                upstream_node_ids=upstream_node_ids,
            )
            scopes.extend(produced)

        emissions = match(parse_query(command.q), scopes)

        limit = max(1, min(command.limit or DEFAULT_LIMIT, MAX_LIMIT))
        returned = emissions[:limit]

        return GetDesignTimeVariableSuggestionsResult(
            workflow_id=command.workflow_id, node_id=command.node_id,
            query=command.q,
            matches=[self._to_match(e) for e in returned],
            scopes=self._summaries(scopes, emissions, returned),
            total_matches=len(emissions),
            truncated=len(emissions) > limit,
        )

    @staticmethod
    def _to_match(e: PathMatch) -> SuggestionMatch:
        scope = e.scope
        if e.path is None:
            # Bare source suggestion — display_path is the open prefix the
            # FE keeps typing into ("{{$variables").
            return SuggestionMatch(
                scope_type=scope.scope_type, scope_id=scope.scope_id,
                scope_label=scope.label, node_type=scope.node_type,
                expression_prefix=scope.expression_prefix,
                path="", kind="scalar", output_type="object",
                label=source_token(scope),
                display_path=scope.expression_prefix,
                match_kind="source",
            )
        p = e.path
        return SuggestionMatch(
            scope_type=scope.scope_type, scope_id=scope.scope_id,
            scope_label=scope.label, node_type=scope.node_type,
            expression_prefix=scope.expression_prefix,
            path=p.path, kind=p.kind, output_type=p.output_type,
            item_type=p.item_type, label=p.label,
            display_path=p.display_path, match_kind=e.match_kind,
        )

    @staticmethod
    def _summaries(
        scopes: list[Scope],
        emissions: list[PathMatch],
        returned: list[PathMatch],
    ) -> list[ScopeSummary]:
        match_counts: dict[int, int] = {}
        returned_counts: dict[int, int] = {}
        for e in emissions:
            match_counts[id(e.scope)] = match_counts.get(id(e.scope), 0) + 1
        for e in returned:
            returned_counts[id(e.scope)] = returned_counts.get(id(e.scope), 0) + 1
        out: list[ScopeSummary] = []
        for s in scopes:
            mc = match_counts.get(id(s), 0)
            rc = returned_counts.get(id(s), 0)
            out.append(ScopeSummary(
                scope_type=s.scope_type, scope_id=s.scope_id, label=s.label,
                expression_prefix=s.expression_prefix,
                match_count=mc, returned_count=rc, truncated=rc < mc,
            ))
        return out

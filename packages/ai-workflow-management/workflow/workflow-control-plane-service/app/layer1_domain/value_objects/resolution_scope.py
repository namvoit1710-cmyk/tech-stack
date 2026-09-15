"""Layered, file-backed variable resolution scope.

Composite + Chain of Responsibility design:

- ``ResolutionScope`` is a Composite holding an ordered tuple of
  ``ScopeFrame`` instances.
- ``ScopeFrame.try_resolve(source)`` is the Chain of Responsibility —
  each frame either claims the expression head (``parsed.source`` from
  ``{{$source.path}}``) by returning a value, or yields ``UNRESOLVED``
  so the next frame gets a turn.

The ordered chain produced by :class:`ResolutionScopeBuilder` is::

    IterationLocalsFrame   →  $loop, $item, mapping $custom, _action
    RunLocalsFrame         →  $node_a, $input, name aliases
    UserVariablesFrame     →  $variable.X
    ParentRunFrame         →  $parent.*, $root.*

Adding a new scope type later (``$tenant``, ``$secret``, ``$env``,
…) is one new frame class plus one builder line — the resolver, the
strategy chain, and every node handler stay unchanged.
"""

from __future__ import annotations

import contextvars
import inspect
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable
from collections.abc import Mapping

from app.layer1_domain.value_objects.node_type import NodeType

if TYPE_CHECKING:
    from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
    from app.layer1_domain.entities.workflow_run import WorkflowRun


# ---------------------------------------------------------------------------
# UNRESOLVED sentinel
#
# Lives here (a value object) instead of in ``variable_resolver`` (a
# service) so that ``resolution_scope`` has zero service-layer
# dependencies. ``variable_resolver`` re-exports the same object for
# backward compatibility — existing imports
# ``from ...resolve.variable_resolver import UNRESOLVED`` keep working
# and ``is UNRESOLVED`` identity checks across modules remain valid.
# ---------------------------------------------------------------------------


class _UnresolvedType:
    _instance: "_UnresolvedType | None" = None

    def __new__(cls) -> "_UnresolvedType":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNRESOLVED"

    def __bool__(self) -> bool:
        return False


UNRESOLVED: _UnresolvedType = _UnresolvedType()


# ---------------------------------------------------------------------------
# ScopeFrame Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ScopeFrame(Protocol):
    """A single layer of the resolution scope chain.

    A frame either claims an expression head — ``parsed.source`` from
    ``{{$source.path}}`` — by returning a value (a dict, a file_ref,
    a scalar, anything the strategy chain knows how to traverse), or
    yields :data:`UNRESOLVED` so the next frame gets a turn.

    Implementations MUST be cheap to construct and side-effect free
    in ``try_resolve``. Heavy work (fetching parent run from the repo,
    loading secrets) is deferred via lazy attributes or done at frame
    construction time, not on every ``try_resolve`` call.
    """

    name: str

    def try_resolve(self, source: str) -> Any: ...

    def known_sources(self) -> list[str]: ...


# ---------------------------------------------------------------------------
# Concrete frames
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IterationLocalsFrame:
    """Per-task overlay: ``$loop``, ``$item``, mapping ``$custom``, ``_action``…

    Frozen dataclass → safe to share across concurrently-running body
    tasks; each task gets its own instance via
    :meth:`ResolutionScope.with_replaced_frame`. Replaces the existing
    ``run.context.variables["loop"] = {...}`` mutation site.
    """

    locals_: Mapping[str, Any] = field(default_factory=dict)
    name: str = "iteration"

    def try_resolve(self, source: str) -> Any:
        return self.locals_[source] if source in self.locals_ else UNRESOLVED

    def known_sources(self) -> list[str]:
        return list(self.locals_.keys())


@dataclass(frozen=True)
class RunLocalsFrame:
    """The current run's ``variables`` dict — node outputs and aliases.

    ``run_locals`` is :attr:`run.context.variables` today. Later phases
    swap this for the view-store mapping when the projector becomes
    authoritative — the frame contract does not change.
    """

    run_locals: Mapping[str, Any] = field(default_factory=dict)
    name: str = "run"

    def try_resolve(self, source: str) -> Any:
        if source in self.run_locals:
            return self.run_locals[source]
        return UNRESOLVED

    def known_sources(self) -> list[str]:
        return list(self.run_locals.keys())


def _top_level_scalars(data: Any) -> dict[str, Any]:
    """Top-level scalar fields of a materialized INPUT-node dict.

    Mirrors the file-backed contribution semantics of the (removed)
    ``_flatten_input_node_output``: keep top-level entries whose key is a
    non-underscore string and whose value is a scalar (drop nested dicts /
    lists — ``$variable`` exposes scalars only). Non-dict input → ``{}``.
    """
    if not isinstance(data, dict):
        return {}
    return {
        k: v
        for k, v in data.items()
        if isinstance(k, str)
        and not k.startswith("_")
        and not isinstance(v, (dict, list))
    }


@dataclass(frozen=True)
class UserVariablesFrame:
    """Custom variables: ``$variable.api_endpoint``, ``$variable.tenant_id``…

    Authors define defaults on ``Workflow.variables``; the StartRun
    caller may override per-run via ``WorkflowRun.user_variables``
    (set on the ROOT run only). Layered, last-wins:
    ``defaults ⊕ overrides ⊕ root-INPUT contributions ⊕ this-run INPUT
    contributions``.

    ``effective`` carries the in-memory layers (defaults ⊕ overrides, and
    — in the eager test/direct path — any pre-resolved ``input_contributions``).
    ``input_refs`` carries the lazy layer: ``(node_id, file_ref)`` pairs for
    completed INPUT nodes (root first, then this run, DAG order so the last
    write wins). On the first ``$variable`` reference the frame materializes
    each ref via the injected async ``fetch`` callable, extracts top-level
    scalars, merges them over ``effective``, and memoises the result for the
    pass. This is the no-``extracted`` OData model: nothing is pre-flattened
    into the data model; scalars are fetched on demand.
    """

    effective: Mapping[str, Any] = field(default_factory=dict)
    input_refs: tuple[tuple[str, Any], ...] = ()
    fetch: Any = None  # Callable[[Any], Awaitable[Any]] | None
    name: str = "variable"
    # Per-pass memo of the merged view. Mutated in place (allowed under
    # frozen); excluded from eq/repr. Shared across loop-body scopes via
    # ``with_replaced_frame`` — idempotent, so a cold-cache double fetch is
    # wasted work, not a correctness bug.
    _memo: dict = field(default_factory=dict, compare=False, repr=False)

    async def try_resolve(self, source: str) -> Any:
        # Reserved single source: ``$variable``. The path after the dot is
        # walked by the downstream resolver strategies.
        if source != "variable":
            return UNRESOLVED
        if "value" in self._memo:
            return self._memo["value"]
        merged = dict(self.effective)
        if self.fetch is not None:
            for _node_id, ref in self.input_refs:
                try:
                    data = await self.fetch(ref)
                except Exception:
                    continue
                merged.update(_top_level_scalars(data))
        self._memo["value"] = merged
        return merged

    def known_sources(self) -> list[str]:
        return ["variable"]


@dataclass(frozen=True)
class SystemRunFrame:
    """``$run.<field>`` — current run's identifiers and lifecycle fields.

    Snapshot is captured by the engine at scope-build time so writes to
    the run aggregate after that point don't change resolved values mid-
    pass. Distinct from ``$variable`` (workflow-author defaults +
    overrides) and ``$variables`` (mutable runtime state).
    """

    snapshot: Mapping[str, Any] = field(default_factory=dict)
    name: str = "system_run"

    def try_resolve(self, source: str) -> Any:
        if source == "run":
            return self.snapshot
        return UNRESOLVED

    def known_sources(self) -> list[str]:
        return ["run"]


@dataclass(frozen=True)
class SystemWorkflowFrame:
    """``$workflow.<field>`` — current workflow's identifiers and metadata."""

    snapshot: Mapping[str, Any] = field(default_factory=dict)
    name: str = "system_workflow"

    def try_resolve(self, source: str) -> Any:
        if source == "workflow":
            return self.snapshot
        return UNRESOLVED

    def known_sources(self) -> list[str]:
        return ["workflow"]


@dataclass(frozen=True)
class SystemVariablesFrame:
    """``$variables.<name>`` — mutable runtime state set by SET_VARIABLE nodes.

    Distinct from ``$variable.<name>`` (singular, design-time + StartRun
    overrides) — this one is the runtime mutable namespace. Backed by
    ``run.context.variables["__vars"]``.

    Returns the raw ``__vars`` value — a file_ref pointer, or a plain dict
    on legacy / pre-projector paths. Path traversal (``$variables.X``) and
    the empty-path ``$variables`` are performed downstream by the async
    strategy chain (``FileBackedResolverStrategy``), which fetches scalars
    on demand via the file service and reuses the per-run fetch cache. No
    inline ``extracted`` read happens in this frame — the no-``extracted``
    OData model relies on the strategy chain, not a pre-materialised cache.
    """

    vars_view: Mapping[str, Any] = field(default_factory=dict)
    name: str = "system_variables"

    def try_resolve(self, source: str) -> Any:
        if source != "variables":
            return UNRESOLVED
        return self.vars_view

    def known_sources(self) -> list[str]:
        return ["variables"]


@dataclass(frozen=True)
class SystemCredentialsFrame:
    """``$credentials.<name>`` — run-time credential map, resolved on demand.

    The frame holds PLAINTEXT once resolved, so it must NEVER be serialised or
    logged — the scope itself is ephemeral (rebuilt per orchestration pass) and
    ``flat_view`` deliberately does not flatten this frame, so ``$parent`` /
    ``$root`` cannot carry a child's secrets sideways.

    Reserved source: ``"credentials"``.  Credentials are self-describing:
    each entry is ``{type, config}`` so materialisation is type-aware and
    centralised in ``materialize_auth_header``.

    **Lazy since SA-2287 (B3).** This frame is the second consumer of the
    credential map — ``TaskNodeHandler`` is the first — and B3 required both to
    move together, or an expression and its own node's worker would disagree
    about what a ref resolves to. So:

    * ``credentials_view`` — the eager layer. After B3 that is the run's
      author-supplied overrides, and pre-B3 runs still carry their full sealed
      map here, which is why an in-flight run keeps resolving.
    * ``fetch`` — the lazy layer: a zero-argument thunk the builder binds to
      (run, workflow). On the FIRST ``$credentials`` reference in a pass the
      workflow's declared refs are fetched from the vault and merged UNDER the
      eager layer (author still wins). A pass that never writes
      ``$credentials`` never pays for it — the common case, since the normal
      way to use a credential is the node's own ``credential_ref``, not an
      expression.

    The thunk is deliberately opaque: this is layer 1, and it must not learn
    what a vault or a provider is to stay lazy.

    Memoised per pass, mirroring :class:`UserVariablesFrame`: a cold-cache
    double fetch under concurrent loop bodies is wasted work, not a
    correctness bug.
    """

    credentials_view: Mapping[str, Any] = field(default_factory=dict)
    fetch: Any = None  # Callable[[], Awaitable[Mapping]] | None
    name: str = "system_credentials"
    _memo: dict = field(default_factory=dict, compare=False, repr=False)

    async def try_resolve(self, source: str) -> Any:
        if source != "credentials":
            return UNRESOLVED
        if "value" in self._memo:
            return self._memo["value"]
        merged = dict(self.credentials_view)
        if self.fetch is not None:
            try:
                fetched = await self.fetch()
            except Exception:
                # Fail-closed to whatever is already known, exactly as the
                # pre-B3 decrypt-or-empty branch did. A node that genuinely
                # needs the secret fails at ITS dispatch with the vault's real
                # reason; an expression must not be the thing that reports it.
                fetched = {}
            merged = {**fetched, **merged}
        self._memo["value"] = merged
        return merged

    def known_sources(self) -> list[str]:
        return ["credentials"]


@dataclass(frozen=True)
class ParentRunFrame:
    """``$parent.*`` and ``$root.*`` for sub-workflow cross-run reads.

    Holds the parent's *resolved* :class:`ResolutionScope`, lazy-built
    once when the child run's orchestration pass starts. ``$root``
    walks the parent chain to the topmost ancestor.
    """

    parent_scope: "ResolutionScope | None" = None
    name: str = "parent"

    def try_resolve(self, source: str) -> Any:
        if self.parent_scope is None:
            return UNRESOLVED
        if source == "parent":
            return self.parent_scope.flat_view()
        if source == "root":
            cursor = self.parent_scope
            while True:
                ancestor = cursor.parent_frame_value()
                if ancestor is None:
                    break
                cursor = ancestor
            return cursor.flat_view()
        return UNRESOLVED

    def known_sources(self) -> list[str]:
        return ["parent", "root"] if self.parent_scope is not None else []


# ---------------------------------------------------------------------------
# ResolutionScope composite
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolutionScope:
    """Ordered composition of :class:`ScopeFrame` instances.

    Frozen → :meth:`with_replaced_frame` returns a new scope; concurrent
    loop bodies and parallel branches never mutate each other's chain.
    """

    frames: tuple[ScopeFrame, ...] = ()
    # Identity / cache keys carried alongside the chain so file-backed
    # caches can key on root_run_id without scanning frames.
    run_id: str = ""
    root_run_id: str = ""
    parent_run_id: str | None = None
    depth: int = 0

    # ---- public API ------------------------------------------------

    async def lookup(self, source: str) -> Any:
        """Walk the frame chain; first frame that claims ``source`` wins.

        ``ScopeFrame.try_resolve`` may be synchronous (pure in-memory
        frames) or a coroutine (frames that fetch scalars on demand via
        the file service — the no-``extracted`` OData model). We await
        only when a frame actually returns an awaitable, so sync frames
        stay zero-overhead and need no protocol change.
        """
        for frame in self.frames:
            result = frame.try_resolve(source)
            if inspect.isawaitable(result):
                result = await result
            if result is not UNRESOLVED:
                return result
        return UNRESOLVED

    def flat_view(self) -> Mapping[str, Any]:
        """Merged view of every frame's locals.

        Used to expose ``$parent`` / ``$root`` as a single dict to the
        TraversalResolverStrategy when an expression like
        ``{{$parent.input.tenant_id}}`` is resolved. Frames walk in
        REVERSE so innermost frames win on conflict (matches
        :meth:`lookup` ordering).
        """
        merged: dict[str, Any] = {}
        for frame in reversed(self.frames):
            if isinstance(frame, RunLocalsFrame):
                merged.update(frame.run_locals)
            elif isinstance(frame, IterationLocalsFrame):
                merged.update(frame.locals_)
            # ParentRunFrame / UserVariablesFrame intentionally NOT
            # flattened — they keep their reserved-source semantics.
        return merged

    def parent_frame_value(self) -> "ResolutionScope | None":
        """Return the parent ResolutionScope held by this scope's
        ParentRunFrame, or ``None`` if there is no ParentRunFrame /
        no parent."""
        for frame in self.frames:
            if isinstance(frame, ParentRunFrame):
                return frame.parent_scope
        return None

    def known_sources(self) -> list[str]:
        out: list[str] = []
        for frame in self.frames:
            out.extend(frame.known_sources())
        return sorted(set(out))

    # ---- builders --------------------------------------------------

    def with_replaced_frame(
        self,
        frame_type: type,
        new_frame: ScopeFrame,
    ) -> "ResolutionScope":
        """Return a copy with the first frame of ``frame_type`` replaced.

        Used by the engine to swap an :class:`IterationLocalsFrame`
        per task without rebuilding the rest of the chain.
        """
        new_frames = tuple(
            new_frame if isinstance(f, frame_type) else f
            for f in self.frames
        )
        return replace(self, frames=new_frames)


# ---------------------------------------------------------------------------
# Builder — single source of truth for canonical frame ordering
# ---------------------------------------------------------------------------


class ResolutionScopeBuilder:
    """Builds a :class:`ResolutionScope` for a given run with the
    canonical frame order. Engine and tests must go through here so
    frame order cannot drift across call sites.

    ``materialize`` is the narrow async fetch callable
    (``file_ref -> materialized dict``, normally ``DataStoreService.materialize``)
    threaded into :class:`UserVariablesFrame` so it can lazily fetch INPUT-node
    scalar contributions for ``$variable.X`` on demand. ``None`` (the default,
    used by unit tests that pass resolved ``input_contributions`` directly)
    disables lazy fetch — the frame then resolves from its in-memory layers
    only.
    """

    def __init__(
        self,
        materialize: Any = None,
        credential_cipher: Any = None,
        resolve_credentials: Any = None,
    ) -> None:
        self._materialize = materialize
        self._credential_cipher = credential_cipher
        # ``resolve_credentials(run, workflow) -> awaitable[{ref: {type, config}}]``
        # — the lazy half of the ``$credentials`` frame (SA-2287 / B3). Narrow
        # callable, not a provider object, so layer 1 keeps knowing nothing
        # about the vault. ``None`` (unit tests, and any deployment with
        # CREDENTIALS_VAULT_URL unset) leaves the frame eager-only.
        self._resolve_credentials = resolve_credentials

    @staticmethod
    def _discover_input_refs(
        run: "WorkflowRun", workflow: "Workflow",
    ) -> tuple[tuple[str, Any], ...]:
        """Ordered ``(node_id, value)`` for completed INPUT nodes.

        DAG topological order so a downstream INPUT's contribution wins over
        an upstream one (last-wins on key collision). ``value`` is the raw
        run-context entry — a file_ref pointer (fetched lazily by the frame)
        or, on mock/legacy paths, a plain dict (passed through by
        ``DataStoreService.materialize``).
        """
        refs: list[tuple[str, Any]] = []
        variables = run.context.variables or {}
        for node in _iter_nodes_in_dag_order(run, workflow):
            if node.node_type not in _INPUT_VARIABLE_NODE_TYPES:
                continue
            value = variables.get(node.id)
            if not value:
                continue
            refs.append((node.id, value))
        return tuple(refs)

    def build(
        self,
        run: "WorkflowRun",
        *,
        workflow: "Workflow",
        parent_scope: ResolutionScope | None = None,
        iteration_locals: Mapping[str, Any] | None = None,
        root_user_variables: Mapping[str, Any] | None = None,
        input_contributions: Mapping[str, Any] | None = None,
        inherited_input_refs: tuple[tuple[str, Any], ...] = (),
    ) -> ResolutionScope:
        """Assemble a :class:`ResolutionScope` for ``run``.

        Effective ``$variable`` view = three layers, last-wins:

        1. **defaults** — ``workflow.variables`` (this run's workflow)
        2. **overrides** — ``root_user_variables`` for sub-workflows
           (root.user_variables ⊕ root INPUT contributions, resolved
           by the engine), or ``run.user_variables`` for top-level runs
           when ``root_user_variables`` is ``None``
        3. **input_contributions** — this run's completed INPUT-node
           submissions (collected by
           :func:`collect_input_contributions`)

        Layer 3 wins over 2, which wins over 1 — runtime user input is
        the freshest signal.
        """
        defaults = dict(getattr(workflow, "variables", {}) or {})
        if root_user_variables is not None:
            overrides = dict(root_user_variables)
        else:
            overrides = dict(getattr(run, "user_variables", {}) or {})

        # ``$variable`` input layer: EAGER when a caller passes a resolved
        # ``input_contributions`` dict (unit tests / direct construction) —
        # merged in-memory, no fetch. Otherwise LAZY (production): discover
        # this run's completed INPUT-node refs and let ``UserVariablesFrame``
        # fetch their scalars on demand via the injected ``materialize``
        # callable. ``inherited_input_refs`` carries the root run's INPUT
        # refs for sub-workflow inheritance (root first → own last, so this
        # run's input wins on collision).
        if input_contributions is not None:
            effective_user_vars = {**defaults, **overrides, **dict(input_contributions)}
            own_input_refs: tuple[tuple[str, Any], ...] = ()
        else:
            effective_user_vars = {**defaults, **overrides}
            own_input_refs = self._discover_input_refs(run, workflow)
        input_refs = tuple(inherited_input_refs) + own_input_refs

        run_snapshot = _snapshot_run(run)
        workflow_snapshot = _snapshot_workflow(workflow)
        vars_view = (run.context.variables or {}).get("__vars") or {}

        # $credentials frame, two layers (SA-2287 / B3).
        #
        # EAGER — the map sealed on the run. Post-B3 that is the caller's
        # ``runtime_credentials``; a run started pre-B3 still carries its fully
        # resolved map here, so it keeps resolving after the deploy.
        credentials_view: dict[str, Any] = {}
        raw_creds = (run.context.variables or {}).get("__credentials")
        if raw_creds is not None and self._credential_cipher is not None:
            try:
                from app.layer1_domain.value_objects.encrypted_credential import EncryptedCredential
                if isinstance(raw_creds, EncryptedCredential):
                    credentials_view = self._credential_cipher.decrypt(raw_creds)
            except Exception:
                pass  # decryption failure → empty frame, fail-closed at worker

        # LAZY — bound per (run, workflow) and only awaited if an expression
        # actually says ``$credentials``. Bound here rather than inside the
        # frame because ``build`` is the only place that holds both.
        credentials_fetch = None
        if self._resolve_credentials is not None:
            def credentials_fetch():  # noqa: F811 - bound thunk, not a redefinition
                return self._resolve_credentials(run, workflow)

        frames: list[ScopeFrame] = [
            IterationLocalsFrame(locals_=iteration_locals or {}),
            RunLocalsFrame(run_locals=run.context.variables),
            UserVariablesFrame(
                effective=effective_user_vars,
                input_refs=input_refs,
                fetch=self._materialize,
            ),
            SystemRunFrame(snapshot=run_snapshot),
            SystemWorkflowFrame(snapshot=workflow_snapshot),
            SystemVariablesFrame(vars_view=vars_view),
            SystemCredentialsFrame(
                credentials_view=credentials_view, fetch=credentials_fetch,
            ),
            ParentRunFrame(parent_scope=parent_scope),
        ]
        return ResolutionScope(
            frames=tuple(frames),
            run_id=run.id,
            root_run_id=run.root_run_id or run.id,
            parent_run_id=run.parent_run_id,
            depth=run.depth_level,
        )

    @staticmethod
    def _effective_user_variables(
        run: "WorkflowRun",
        workflow: "Workflow",
    ) -> dict[str, Any]:
        """Workflow defaults overridden by run's user_variables.

        Only the *root* run carries authoritative ``user_variables`` —
        but for top-level runs root == self so reading ``run`` is
        correct. Engine callers handling sub-workflows should pass
        ``root_user_variables`` explicitly to :meth:`build` instead of
        relying on this helper.
        """
        defaults = dict(getattr(workflow, "variables", {}) or {})
        overrides = dict(getattr(run, "user_variables", {}) or {})
        return {**defaults, **overrides}


# ---------------------------------------------------------------------------
# INPUT-node contributions to ``$variable.*``
#
# Beyond ``Workflow.variables`` defaults and ``WorkflowRun.user_variables``
# overrides, the third source feeding ``UserVariablesFrame.effective`` is
# completed user-input node submissions. Flat namespacing — an INPUT
# node submitting ``{"name": "Bob"}`` exposes ``$variable.name`` —
# matches the precedent set by ``Workflow.variables`` keys, so authors
# don't have to remember "did this come from the schema or the user?".
# Collisions resolve **last-wins by DAG topological order**: a downstream
# INPUT overrides an upstream one with the same key.
# ---------------------------------------------------------------------------


# Phase 1 scope: INPUT only. HUMAN_ACTION / FILE_UPLOAD remain reachable
# via the ``$node_id.*`` path; promoting them to ``$variable`` is a one-
# line change to this set + tests.
_INPUT_VARIABLE_NODE_TYPES: frozenset[NodeType] = frozenset({NodeType.INPUT})


def _iter_nodes_in_dag_order(
    run: "WorkflowRun",
    workflow: "Workflow",
) -> Any:
    """Yield ``workflow.nodes`` in a deterministic topological order.

    Uses Kahn's algorithm against ``run.context.topology.predecessors``
    when the dag info is populated; falls back to the workflow's
    ``nodes`` list order when it isn't (tests that build a Workflow
    without running ``TopologyBuilder.prepare``). Within a topological
    layer, nodes are emitted in their ``workflow.nodes`` index order
    so the iteration is stable across invocations.
    """
    predecessors = (
        getattr(getattr(run.context, "topology", None), "predecessors", None) or {}
    )
    if not predecessors:
        # Fallback: stable iteration in author-defined order.
        yield from workflow.nodes
        return

    # Stable order tie-breaker — nodes appear in workflow.nodes order.
    node_index: dict[str, int] = {n.id: i for i, n in enumerate(workflow.nodes)}
    by_id: dict[str, "WorkflowNode"] = {n.id: n for n in workflow.nodes}

    # Build remaining-pred count using the predecessors map. Nodes
    # missing from predecessors are treated as roots (no incoming edges).
    remaining: dict[str, int] = {
        n.id: len(predecessors.get(n.id, []) or [])
        for n in workflow.nodes
    }
    ready: deque[str] = deque(
        sorted((nid for nid, c in remaining.items() if c == 0),
               key=lambda nid: node_index.get(nid, 0))
    )

    # Build forward adjacency from the predecessors map (cheap inversion).
    forward: dict[str, list[str]] = {n.id: [] for n in workflow.nodes}
    for child_id, parents in predecessors.items():
        for parent_id in parents or []:
            if parent_id in forward and child_id in remaining:
                forward[parent_id].append(child_id)

    visited: set[str] = set()
    while ready:
        nid = ready.popleft()
        if nid in visited:
            continue
        visited.add(nid)
        node = by_id.get(nid)
        if node is not None:
            yield node
        # Discover next-ready successors in stable order.
        next_ready: list[str] = []
        for succ in forward.get(nid, []):
            if succ in visited:
                continue
            remaining[succ] = max(0, remaining[succ] - 1)
            if remaining[succ] == 0:
                next_ready.append(succ)
        next_ready.sort(key=lambda sid: node_index.get(sid, 0))
        ready.extend(next_ready)

    # Cycle / disconnected nodes — emit any leftover so collection
    # never silently drops INPUT submissions in malformed graphs.
    for n in workflow.nodes:
        if n.id not in visited:
            yield n


# ---------------------------------------------------------------------------
# Run / workflow snapshots for SystemRunFrame / SystemWorkflowFrame
# ---------------------------------------------------------------------------


_RUN_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "id", "workflow_id", "status", "started_at", "completed_at",
    "test_mode", "parent_run_id", "parent_node_id", "depth_level", "root_run_id",
)

_WORKFLOW_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "id", "name", "version", "description", "status", "purpose",
    "created_at", "updated_at",
)


def _snapshot_run(run: "WorkflowRun") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _RUN_SNAPSHOT_FIELDS:
        val = getattr(run, key, None)
        out[key] = getattr(val, "value", val) if val is not None else None
    return out


def _snapshot_workflow(workflow: "Workflow") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _WORKFLOW_SNAPSHOT_FIELDS:
        val = getattr(workflow, key, None)
        out[key] = getattr(val, "value", val) if val is not None else None
    return out


# ---------------------------------------------------------------------------
# Ambient contextvar (mirrors ``_current_run_id_var`` /
# ``_current_scope_stack_var`` patterns elsewhere in layer1_domain).
# ---------------------------------------------------------------------------


_current_scope_var: contextvars.ContextVar[ResolutionScope | None] = (
    contextvars.ContextVar("current_scope", default=None)
)


def set_current_scope(scope: ResolutionScope | None) -> contextvars.Token:
    """Install ``scope`` on the active asyncio task's context.

    Returns a token that must be passed to :func:`reset_current_scope`
    in a ``finally`` block so nested calls don't leak.
    """
    return _current_scope_var.set(scope)


def reset_current_scope(token: contextvars.Token) -> None:
    """Restore the previous ambient scope using a token from
    :func:`set_current_scope`."""
    _current_scope_var.reset(token)


def get_current_scope() -> ResolutionScope | None:
    """Read the ambient scope. Used by
    :func:`VariableResolver._resolve_single`'s overlay fallback path
    when the legacy dict context did not hold the requested source."""
    return _current_scope_var.get()

"""Resolver strategy for file-backed data references.

When a node's output is stored as a CSV file (``__file_ref`` dict),
this strategy first checks the ``extracted`` cache for the requested
path.  On a cache miss it tries OData single-row fetch for array index
access, then falls back to full materialization.

``try_resolve`` and its helpers are ``async def``; callers must await
the resolver chain so file-service I/O does not block the event loop.

Cache lifecycle
~~~~~~~~~~~~~~~

Full-file materialize results and OData single-row query results are
both cached **per run_id**, not per orchestration pass.  The run_id is
read from the ``variable_resolver._current_run_id_var`` contextvar set
by the orchestration engine before each pass.  Multiple expressions in
the same run touching the same file_ref therefore trigger exactly one
download; subsequent resolutions read from memory.

When a run reaches a terminal state, the engine calls
``drop_run_cache(run_id)`` to release the memory for that run.  Calls
made without an active run_id (e.g., unit tests) fall through to a
shared ``None``-keyed cache that is cleared via the legacy
``clear_materialize_cache()`` helper.
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from typing import Any, TYPE_CHECKING

from app.layer1_domain.entities.data_reference import build_file_ref, is_file_ref
from app.layer1_domain.value_objects.value_kind import (
    ARRAY,
    OBJECT,
    is_collection,
    kind_of,
)
from app.layer2_application.resolve.variable_resolver import (
    UNRESOLVED,
    VariableResolutionError,
    get_current_run_id,
)
from app.layer1_domain.value_objects.resolution_scope import get_current_scope

from app.layer2_application.data.data_store_service import MaterializeTooLargeError
from app.layer2_application.interfaces.cache_budget_registry import (
    default_cache_budget_registry,
)


class _ArtifactMiss:
    """No child artifact owns the requested field.

    Distinct from ``UNRESOLVED``, which now means *an artifact DOES own the
    field and the answer inside it is "not found"*. That answer is final: the
    child file holds the entire value of the field (``_owns_whole_field``
    guarantees it), so materializing the parent could only produce the same
    result at the cost of downloading the whole tree. Collapsing the two is
    what made ``{{$node.orders.name}}`` — a string key on a table, i.e. a
    typo — pay for a full download to arrive at UNRESOLVED.

    Falsy, so any truthiness check treats it like the miss it is.
    """

    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<ARTIFACT_MISS>"


_ARTIFACT_MISS = _ArtifactMiss()


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    """Bounded int from the environment, tolerating garbage.

    Duplicated from ``layer4_frameworks.caching.bounded_ttl_cache`` on purpose:
    this module is layer2 and layer4 imports are one-directional here (131
    layer4→layer2 imports, zero the other way). Seven lines is cheaper than
    inverting the dependency for a config read.
    """
    raw = os.getenv(name, "")
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        _logger.warning("%s=%r is not an integer — using %d", name, raw, default)
        return max(minimum, default)


# SA-2262 — ceiling on what the run-scoped materialize cache may retain, in
# ESTIMATED bytes, across every in-flight run. 0 disables the cap (legacy
# unbounded behaviour) so it can be turned off without a rollback.
_MATERIALIZE_CACHE_MAX_BYTES = _int_env(
    "RESOLVE_MATERIALIZE_CACHE_MAX_BYTES", 64 * 1024 * 1024,
)
# Parsed structures run 5.5-9.3x the CSV bytes in this codebase; take the
# pessimistic end, because a cap that under-estimates is not a cap.
_MATERIALIZE_PARSE_AMPLIFICATION = _int_env(
    "RESOLVE_MATERIALIZE_PARSE_AMPLIFICATION", 9, minimum=1,
)
# A ref that declares no size is charged the whole budget: unmeasurable must
# fail closed, not read as free.
_MATERIALIZE_UNKNOWN_CHARGE = max(1, _MATERIALIZE_CACHE_MAX_BYTES)

if TYPE_CHECKING:
    from app.layer2_application.data.data_store_service import DataStoreService

_logger = logging.getLogger(__name__)

# SA-1949 — bounds on the "does this plain container hold a file_ref?" probe.
# Depth 2 covers the real shapes ({"result": <ref>}, {"branchA": {"items": <ref>}});
# the width cap keeps the probe O(1) on a big materialized list.
_NESTED_REF_SCAN_DEPTH = 2
_NESTED_REF_SCAN_WIDTH = 8


def _carries_file_ref(value: Any, _depth: int = 0) -> bool:
    """True when *value* is a container holding a file_ref within a few levels.

    SA-1949 — the gate for descending into a non-file_ref source. Deliberately
    cheap and shallow: the shapes that need it nest a reference one or two levels
    down (``{"result": <ref>}`` from an inline-output node, ``{"branchA": <ref>}``
    from a MERGE), and this runs on every unresolved expression. A deep scan of a
    materialized 60k-row list is exactly the cost we are here to avoid, so lists
    are probed only at their head.
    """
    if _depth > _NESTED_REF_SCAN_DEPTH:
        return False
    if is_file_ref(value):
        return True
    if isinstance(value, dict):
        return any(_carries_file_ref(v, _depth + 1) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(
            _carries_file_ref(v, _depth + 1)
            for v in value[:_NESTED_REF_SCAN_WIDTH]
        )
    return False


class FileBackedResolverStrategy:
    """Resolve variable paths against file-backed references."""

    def __init__(
        self,
        data_store_service: DataStoreService,
        file_client: Any = None,
        budget_registry: Any = None,
        schema_dictionary: Any = None,
    ) -> None:
        self._store = data_store_service
        self._file_client = file_client
        # AIW_SCHEMA_DEF lookup — see ``data/schema_dictionary.py``. Optional and
        # unused while ``SCHEMA_REF_MODE=off``: 90.4% of a file_ref's bytes on
        # the wire are ``columns``/``column_types``, which this can supply from
        # the ``schema_hash`` every ref already carries. ``shadow`` verifies that
        # claim against real traffic before a single byte is removed.
        self._schema_dictionary = schema_dictionary
        # Per-run materialization cache: run_id → (file_id → materialized data).
        # Populated on first full-file download; reused by every subsequent
        # expression in the same run.  Entries for a run are dropped via
        # ``drop_run_cache(run_id)`` when the run reaches a terminal state.
        self._run_materialize_caches: dict[str | None, dict[str, Any]] = {}
        # Per-run OData single-row cache: run_id → ((file_id, query_key) → rows).
        # Eliminates repeat OData calls for the same file/query within a run.
        self._run_odata_caches: dict[str | None, dict[tuple[str, tuple], dict[str, Any]]] = {}
        # SA-2262 — byte accounting for the cap. Insertion-ordered, so the
        # oldest insertion is evicted first when the budget is exceeded.
        self._materialize_costs: OrderedDict[tuple[Any, str], int] = OrderedDict()
        self._materialize_bytes = 0
        self._materialize_evictions = 0
        # The budget's largest line reports itself (SA-2256).
        (default_cache_budget_registry() if budget_registry is None
         else budget_registry).register(self, "run_scoped_materialize")

    @staticmethod
    def _current_cache_key() -> str | None:
        """Cache bucket key.

        Prefers ``root_run_id`` from the ambient :class:`ResolutionScope`
        so a parent and its sub-workflows share one materialize/OData
        bucket — same ``file_id`` referenced from a parent expression
        and a child expression downloads exactly once. Falls back to
        the legacy per-run-id contextvar when no scope is set (tests,
        startup, callers that don't go through the engine wiring).
        """
        scope = get_current_scope()
        if scope is not None and scope.root_run_id:
            return scope.root_run_id
        return get_current_run_id()

    def _get_materialize_cache(self) -> dict[str, Any]:
        """Return the materialize cache bucket for the current root run."""
        cache_key = self._current_cache_key()
        bucket = self._run_materialize_caches.get(cache_key)
        if bucket is None:
            bucket = {}
            self._run_materialize_caches[cache_key] = bucket
        return bucket

    # ------------------------------------------------------------------
    # SA-2262 — the materialize cache has a ceiling
    # ------------------------------------------------------------------

    @staticmethod
    def _retained_bytes_estimate(source_data: dict[str, Any]) -> int:
        """Cheap O(1) estimate of what caching this value will retain.

        Deliberately NOT ``inline_output_size_bytes``: that json-dumps the whole
        value, so *measuring* a large materialized structure would allocate a
        second copy of it — doubling the peak this cap exists to lower.

        The proxy is the ref's declared CSV ``size_bytes`` multiplied by a
        parse-amplification factor. The 5.5-9.3x range is measured for
        ``dict_to_csv`` round-trips in this codebase; the default takes the
        pessimistic end, because a cap that under-estimates is not a cap. An
        unmeasurable ref is charged the full remaining budget rather than
        treated as free — failing closed.
        """
        try:
            declared = int(source_data.get("size_bytes") or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared <= 0:
            return _MATERIALIZE_UNKNOWN_CHARGE
        return declared * _MATERIALIZE_PARSE_AMPLIFICATION

    def _remember_materialized(
        self, bucket: dict[str, Any], file_id: str, value: Any,
        source_data: dict[str, Any],
    ) -> None:
        """Cache *value*, evicting least-recently-inserted entries past the cap.

        The run-scoped materialize cache was unbounded: every distinct file a
        run touched stayed resident until the run reached a terminal state, and
        the budget line for it (~80MB/run x ORCHESTRATION_WORKER_COUNT) is what
        makes 256MB unreachable. It is now capped in bytes across ALL runs, so
        the ceiling holds no matter how many runs are in flight.

        Evicting only costs a re-read on a later hit; NOT evicting costs the
        pod. Terminal eviction (``drop_run_cache``) is still what normally
        frees a run - this is the backstop for runs still in flight.
        """
        cost = self._retained_bytes_estimate(source_data)
        bucket[file_id] = value
        self._materialize_bytes += cost
        self._materialize_costs[(self._current_cache_key(), file_id)] = cost
        self._evict_materialize_until_within_cap()

    def _evict_materialize_until_within_cap(self) -> None:
        if _MATERIALIZE_CACHE_MAX_BYTES <= 0:
            return
        while (
            self._materialize_bytes > _MATERIALIZE_CACHE_MAX_BYTES
            and self._materialize_costs
        ):
            (run_key, file_id), cost = next(iter(self._materialize_costs.items()))
            self._materialize_costs.pop((run_key, file_id), None)
            self._materialize_bytes = max(0, self._materialize_bytes - cost)
            run_bucket = self._run_materialize_caches.get(run_key)
            if run_bucket is not None:
                run_bucket.pop(file_id, None)
            self._materialize_evictions += 1
            _logger.debug(
                "materialize cache over budget — evicted file_id=%s from run=%s "
                "(~%d bytes reclaimed, cap=%d)",
                file_id, run_key, cost, _MATERIALIZE_CACHE_MAX_BYTES,
            )

    def _forget_run_costs(self, run_id: str | None) -> None:
        for key in [k for k in self._materialize_costs if k[0] == run_id]:
            self._materialize_bytes = max(
                0, self._materialize_bytes - self._materialize_costs.pop(key),
            )

    def _get_odata_cache(self) -> dict[tuple[str, tuple], dict[str, Any]]:
        """Return the OData row cache bucket for the current root run."""
        cache_key = self._current_cache_key()
        bucket = self._run_odata_caches.get(cache_key)
        if bucket is None:
            bucket = {}
            self._run_odata_caches[cache_key] = bucket
        return bucket

    def budget_snapshot(self) -> dict:
        """Report the run-scoped materialize / OData caches (SA-2256).

        This is the budget's biggest line — ~80MB per run × ORCHESTRATION_
        WORKER_COUNT. ``materialize_bytes_estimate`` is exactly that: an
        ESTIMATE (declared CSV size × parse amplification), named so nobody
        reads it as a measurement. The authoritative bytes come from P0-3.
        """
        return {
            "materialize_buckets": len(self._run_materialize_caches),
            "materialize_entries": sum(
                len(b) for b in self._run_materialize_caches.values()
            ),
            "materialize_bytes_estimate": self._materialize_bytes,
            "materialize_bytes_cap": _MATERIALIZE_CACHE_MAX_BYTES,
            "materialize_evictions": self._materialize_evictions,
            "odata_buckets": len(self._run_odata_caches),
            "odata_entries": sum(len(b) for b in self._run_odata_caches.values()),
        }

    def drop_run_cache(self, run_id: str | None) -> None:
        """Release all cached data for *run_id*.

        Called by the orchestration engine when a run reaches a terminal
        state (COMPLETED / FAILED / CANCELLED) so long-lived deployments
        don't leak memory from completed runs.
        """
        self._run_materialize_caches.pop(run_id, None)
        self._run_odata_caches.pop(run_id, None)
        self._forget_run_costs(run_id)

    def clear_materialize_cache(self) -> None:
        """Legacy shim: clears only the cache bucket keyed by ``None``.

        Pre-Phase-1 code called this at every orchestration-pass drain to
        avoid unbounded cache growth. Now that caches are run-scoped, the
        drain no longer needs to wipe anything — but tests that call the
        method on a strategy instance still expect the no-run-id bucket
        to be cleared, so we honour that narrow contract here.
        """
        self._run_materialize_caches.pop(None, None)
        self._run_odata_caches.pop(None, None)

    # Path prefixes that access the file reference metadata instead of CSV data.
    _META_KEYS = frozenset({
        "__file_id", "__artifacts", "__artifact",
        "__content_type", "__row_count", "__columns",
    })

    def _apply_schema_mode(self, ref: Any) -> None:
        """``shadow``: report what a hash-only ref would lose. ``on``: expand it.

        ``off`` (the default) returns before touching anything, so this costs an
        attribute read on the hot path and nothing else.

        Never raises. A schema lookup failing must degrade to "use the inline
        copy", which is exactly what every reader already does for refs written
        before ``schema_hash`` existed — the expensive difference being that
        their fallback GUESSES types (``CsvConverter._unflatten`` →
        ``_try_parse_value``) rather than reporting a problem. That is the
        failure this mode exists to detect before it can happen.
        """
        dictionary = self._schema_dictionary
        if dictionary is None:
            return
        try:
            from app.layer2_application.data import schema_dictionary as _sd

            if _sd.observing():
                problems = _sd.verify(ref, dictionary)
                if problems:
                    _logger.warning(
                        "schema-dictionary divergence file_id=%s: %s",
                        (ref.get("file_id") if isinstance(ref, dict) else "?"),
                        ", ".join(problems[:6]),
                    )
            elif _sd.enforcing():
                _sd.expand(ref, dictionary)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("schema-dictionary check skipped: %s", exc)

    async def try_resolve(
        self,
        source_data: Any,
        path_parts: tuple[str | int, ...],
    ) -> Any:
        if not is_file_ref(source_data):
            # SA-1949 — the source may be a PLAIN DICT that *carries* a file_ref:
            # an inline-output node (code / approval_flow) skips ``store_data``,
            # so when worker-side streaming replaces its big field with a pointer
            # the node output becomes ``{"result": <file_ref>}`` — a dict at the
            # top level with a reference nested inside.
            #
            # ``_resolve_through`` (SA-1905 F1) already knows how to descend into
            # a nested reference, but it was only reachable *after* this gate —
            # so for these nodes it could never run. The chain then fell through
            # to TraversalResolverStrategy, which int-indexed the reference dict
            # and failed the run with "Expected list at index 0, got dict".
            #
            # Hand the walk to F1 instead. On a genuine miss it returns
            # UNRESOLVED, so the chain still falls through to Metadata/Traversal
            # and their error messages and flat-key lookup are preserved.
            if path_parts and _carries_file_ref(source_data):
                return await self._resolve_through(source_data, path_parts)
            return UNRESOLVED

        # SCHEMA_REF_MODE. Runs on every file-backed resolve — the one place a
        # descriptor is about to have its schema READ (``_unflatten`` casts by
        # ``column_types`` at :745, ``__columns`` is served at :849, and :505
        # treats a non-empty ``columns`` as "not empty").
        self._apply_schema_mode(source_data)

        # If the caller wants the whole source (empty path), materialize.
        # SA-1942 — effectively dead through ``VariableResolver`` (an empty path
        # returns the file_ref pointer before any strategy runs), but guarded
        # defensively so a direct caller can't OOM the control plane either.
        if not path_parts:
            try:
                # Empty path = the whole value. Name it, rather than letting
                # the log fall back to the file_id and look like the deep-path case.
                return await self._store.materialize_guarded(
                    source_data, context="<whole value>",
                )
            except MaterializeTooLargeError as exc:
                raise VariableResolutionError(str(exc)) from exc

        first = path_parts[0]

        # --- Meta access: dunder-prefixed paths read the file ref dict ---
        if isinstance(first, str) and first in self._META_KEYS:
            return self._resolve_meta(source_data, first, path_parts[1:])

        # --- Normal CSV data access ---
        # Build a dot-joined cache key
        cache_key = ".".join(str(p) for p in path_parts)
        extracted = source_data.get("extracted", {})

        # 1. Check extracted cache (pre-populated scalars)
        entry = extracted.get(cache_key)
        if isinstance(entry, dict) and "value" in entry:
            return entry["value"]

        # 1b. Check if first path segment matches an array_field artifact
        if isinstance(first, str):
            artifact_value = await self._try_resolve_artifact(
                source_data, first, path_parts[1:],
            )
            if artifact_value is not _ARTIFACT_MISS:
                # Includes UNRESOLVED — see ``_ArtifactMiss``. An artifact that
                # owns the field owns the ANSWER, "not found" included.
                return artifact_value

        # 2. Try OData single-row fetch for array index access
        row_index = self._find_first_int_index(path_parts)
        if row_index is not None and self._file_client is not None:
            value = await self._try_odata_row_fetch(source_data, path_parts, row_index)
            if value is not UNRESOLVED:
                extracted[cache_key] = {"value": value}
                return value

        # 2b. SA-2262 — provably unanswerable without downloading anything.
        #     A ref that DECLARES an array root cannot answer a string key:
        #     both ``_traverse`` and ``_resolve_through`` return UNRESOLVED the
        #     instant a string part meets a non-dict. Downloading N rows to
        #     compute "not found" was the dominant ``flat_full_download`` cause
        #     — measured at 500 rows fetched for a one-word answer.
        #
        #     Behaviour change, deliberate and narrow: this path used to be
        #     able to raise the "download likely failed" diagnostic below,
        #     because it did download. That error was only ever reachable via a
        #     fetch this branch proves is unnecessary — the answer is UNRESOLVED
        #     whether or not the download would have succeeded.
        #
        #     Fires ONLY on a declared array (SA-1977). A legacy ref reports
        #     UNKNOWN and keeps the old path: never guess a kind the writer did
        #     not write down.
        if isinstance(first, str) and is_collection(source_data):
            return UNRESOLVED

        # 2c. A key that lives in the MAIN CSV of an OBJECT root. That CSV is
        #     exactly ONE row, so a single OData page answers the whole path —
        #     where the fallback below downloads the main file AND every child
        #     table to reach the same scalar. Measured on a 200-order graph:
        #     ``{{$node.title}}`` cost 3 downloads / 9.7 KB to return one word.
        #
        #     Gated by the same completeness rule as the index tier, so a key
        #     that actually lives in a child file is never answered from a row
        #     that cannot contain it. On a miss this falls through to the full
        #     materialize below, so a wrong guess costs a query, never an answer.
        if (
            isinstance(first, str)
            and self._file_client is not None
            and source_data.get("file_id")
            and kind_of(source_data) == OBJECT
            and self._row_answers_path(source_data, path_parts)
            and self._main_csv_within_cap(source_data)
        ):
            value = await self._try_odata_row_fetch(
                source_data, (0,) + tuple(path_parts), 0,
            )
            if value is not UNRESOLVED:
                extracted[cache_key] = {"value": value}
                return value

        # 3. Full materialize (with per-pass cache to avoid repeated downloads).
        #    SA-1942 — the reachable whole-file download: a deep NON-index path
        #    into an oversized file-backed value (e.g. a big JSON blob) is
        #    refused with an actionable error instead of OOMing the control
        #    plane. Index access (step 2) already reads a single OData row.
        try:
            materialized = await self._cached_materialize(
                source_data, context=".".join(str(p) for p in path_parts),
            )
        except MaterializeTooLargeError as exc:
            raise VariableResolutionError(
                f"cannot resolve path "
                f"{'.'.join(str(p) for p in path_parts)!r}: {exc}"
            ) from exc
        value = await self._resolve_through(materialized, path_parts)

        # Update extracted cache for future resolution
        if value is not UNRESOLVED:
            extracted[cache_key] = {"value": value}
            return value

        # Path not found in the materialized content. Distinguish two cases:
        #  - the file genuinely has no such key (a real authoring error) →
        #    return UNRESOLVED so the chain reports "key not found in <data>".
        #  - the file FAILED to materialize (download returned None/empty →
        #    DataStoreService.materialize yields {}) even though the file_ref
        #    declares rows/columns/artifacts → the download failed
        #    (connection/auth/404), and silently treating it as empty surfaces
        #    the misleading "Key 'X' not found. Available keys: [__file_ref,
        #    file_id, …]" after the chain falls through to traversing the raw
        #    envelope. Raise the real cause instead.
        if self._materialize_failed(source_data, materialized):
            raise VariableResolutionError(
                f"file-backed source (file_id={source_data.get('file_id', '')!r}) "
                f"materialized to EMPTY content while its reference declares "
                f"row_count={source_data.get('row_count')} "
                f"columns={source_data.get('columns')} — the file download "
                f"likely failed (connection / auth / missing file); cannot "
                f"resolve path {'.'.join(str(p) for p in path_parts)!r}. "
                f"Check the control-plane 'file-service download' log line for "
                f"this file_id (HTTP 401/403 = auth, 404 = missing, "
                f"ConnectError = transient)."
            )

        return value

    @staticmethod
    def _materialize_failed(source_data: dict[str, Any], materialized: Any) -> bool:
        """Empty materialization while the file_ref declares content = a failed
        download, not a genuinely empty file.

        Returns False for a legitimately empty ref (row_count=0, no columns,
        no artifacts) so empty INPUT/optional data still resolves to UNRESOLVED
        as before.
        """
        if materialized not in (None, {}, []):
            return False
        return bool(
            (source_data.get("row_count") or 0)
            or (source_data.get("columns") or [])
            or (source_data.get("artifacts") or [])
        )

    # ------------------------------------------------------------------
    # Array artifact resolution
    # ------------------------------------------------------------------

    # Artifact types whose child file IS an array of objects addressable on its
    # own — so ``{{$node.<field>}}`` can be answered with a POINTER and
    # ``{{$node.<field>[i]}}`` with a single OData row, neither of which
    # downloads anything. ``array_field`` is the legacy depth-1 split;
    # ``nested_array`` is the recursive split (SA-1910); ``nested_array_field``
    # is what ``schema_artifact_provisioner`` writes for a depth>0 declared
    # table. Only ``array_field`` was listed here, which is why turning
    # ``RECURSIVE_ARTIFACT_SPLIT`` on silently disabled BOTH lazy tiers and sent
    # every ``{{$node.<field>}}`` through a full download of the whole tree.
    _ARRAY_ARTIFACT_TYPES = frozenset(
        {"array_field", "nested_array", "nested_array_field"}
    )

    @staticmethod
    def _owns_whole_field(source_data: dict[str, Any], art: dict[str, Any]) -> bool:
        """Is *art*'s file the WHOLE value of its field, or one parent's slice?

        An ``array_field`` is split out of a single object, so its file IS the
        field. A recursive-split child is different in kind: it holds the rows
        of EVERY parent row concatenated, each stamped ``__parent_row_id``
        (which is exactly what ``parent_row_key_column`` announces). That file
        equals ``<field>`` only when the parent has ONE row — an OBJECT root.

        On an ARRAY root ``{{$node.<field>}}`` is not a value at all (a list has
        no string key), so declining here is not a loss: the SA-2262
        short-circuit downstream answers UNRESOLVED — the same answer, minus the
        download. Claiming it instead would hand the caller every parent's rows
        glued together, which is wrong in a way nothing downstream could detect.
        """
        if not art.get("parent_row_key_column"):
            return True
        kind = kind_of(source_data)
        if kind == OBJECT:
            return True
        if kind == ARRAY:
            return False
        # Legacy ref with no declared kind: claim it only when the ref itself
        # says one row. Never guess a kind the writer did not write down.
        return int(source_data.get("row_count") or 0) <= 1

    @staticmethod
    def _artifact_pointer(art: dict[str, Any]) -> dict[str, Any]:
        """Mint a standalone ref for *art* so a consumer can read it lazily.

        SA-1977 — carry the DECLARATION through. Dropping the kind here would
        hand the loop (and every other consumer of ``{{$node.items}}``) a
        pointer that says nothing, so a 1-row artifact would materialize as a
        bare dict again while ``materialize($node)["items"]`` gave a list: two
        answers for the same bytes.

        ``artifacts`` and ``columns`` are carried for the same reason. A
        recursive-split child owns its OWN grandchildren; minting a pointer
        without them would silently truncate the value at depth 2, and a
        provisioned artifact is the only place the declared column list lives.
        Both are empty on the legacy depth-1 split, so that path is unchanged.

        Kind falls back to ``array<object>`` rather than to nothing. Every type
        in ``_ARRAY_ARTIFACT_TYPES`` IS a table of objects by construction —
        ``store_data`` already writes exactly that declaration on the artifacts
        it creates — but ``schema_artifact_provisioner`` predates SA-1977 and
        writes none. Leaving those empty makes the pointer fall back to the
        legacy row-count guess, so a ONE-row child would read back as a bare
        dict here while ``materialize(parent)[field]`` gave a 1-element list:
        two answers for the same bytes, which is the defect SA-1977 exists to
        remove. The artifact TYPE is the declaration.
        """
        return build_file_ref(
            file_id=art.get("file_id", ""),
            row_count=art.get("row_count", 0),
            content_type=art.get("content_type", "text/csv"),
            columns=list(art.get("columns") or []),
            column_types=art.get("column_types"),
            artifacts=list(art.get("artifacts") or []),
            value_kind=art.get("value_kind") or ARRAY,
            item_kind=art.get("item_kind") or OBJECT,
        )

    async def _try_resolve_artifact(
        self,
        source_data: dict[str, Any],
        field_name: str,
        remaining_path: tuple[str | int, ...],
    ) -> Any:
        """Answer ``<field_name>`` from a child artifact without downloading.

        Empty path (``{{$node.b}}``) → a file_ref pointer to the child, so
        workers and LOOP nodes fetch it directly. Remaining path → the rest is
        handed back to the full ladder rooted at that pointer, which reuses the
        OData single-row tier and the child's own artifact tier.

        Returns ``_ARTIFACT_MISS`` when no artifact owns *field_name*; anything
        else (``UNRESOLVED`` included) is the final answer for this field.
        """
        artifacts = source_data.get("artifacts", [])
        for art in artifacts:
            if art.get("name") != field_name:
                continue
            if art.get("artifact_type") not in self._ARRAY_ARTIFACT_TYPES:
                continue
            if not art.get("file_id"):
                continue
            if not self._owns_whole_field(source_data, art):
                continue

            pointer = self._artifact_pointer(art)
            if not remaining_path:
                return pointer

            # Recurse through the pointer rather than fetching the row here.
            # The local fetch this replaces had three defects the shared ladder
            # does not: it never stripped ``__row_id``/``__parent_row_id``/
            # ``_row_index`` (so ``{{$node.items[0]}}`` leaked synthetic
            # columns), it never unwrapped the reserved ``__value`` column, and
            # it read the child CSV alone — so ``orders[0].items`` looked for a
            # grandchild in a row that cannot contain one. ``try_resolve``
            # handles all three, and short-circuits a string key on a declared
            # array without any I/O at all.
            return await self.try_resolve(pointer, remaining_path)
        return _ARTIFACT_MISS

    # ------------------------------------------------------------------
    # OData single-row fetch
    # ------------------------------------------------------------------

    def _main_csv_within_cap(self, ref: dict[str, Any]) -> bool:
        """May the single-row fast path read this ref's main CSV?

        On an OBJECT root the main CSV is ONE row, so "fetch one OData page"
        and "download the whole main file" are the same act — and that file can
        be a multi-megabyte JSON blob in a single cell. Without this check the
        cheap path is a way AROUND the SA-1942 cap: the guarded materialize
        refuses the value while the fast path, which runs first, hands it over.

        Only the MAIN file is charged. The child tables are exactly what this
        path avoids downloading, so making it answer for their bytes would
        disable it on precisely the graphs it helps most.

        A ref that declares no size — written before ``size_bytes`` was stamped
        — is NOT assumed small. With the cap on it falls through to the guarded
        materialize, which probes the real size before committing.
        """
        cap = int(getattr(self._store, "_materialize_max_bytes", 0) or 0)
        if cap <= 0:  # guard disabled by config — same answer as materialize
            return True
        declared = int(ref.get("size_bytes") or 0)
        return 0 < declared <= cap

    @staticmethod
    def _row_answers_path(
        source_data: dict[str, Any], after_index: tuple[str | int, ...],
    ) -> bool:
        """Can ONE flat OData row answer this path, or does it need a re-join?

        A recursive-split ref stores each row's array-of-object fields in CHILD
        files keyed by ``__parent_row_id``. A single OData row therefore carries
        the row's scalars and NONE of its child tables — so answering
        ``{{$node[5]}}`` from it returns a row silently missing every nested
        field, while ``materialize()[5]`` on the same bytes returns it complete.
        Two answers for one value, and the cheap one wins because it runs first.

        The row is sufficient when the ref has no re-joined children at all
        (the flat case, which is the common one), or when the path descends into
        a column the row actually carries. Asking for the WHOLE row, or for a
        field that lives in a child file, needs the full re-join — so decline
        and let the caller fall through to materialize.

        Compared on the artifact name's FIRST SEGMENT, because a recursive-split
        child is named by its DOTTED path: ``{"order": {"lines": [...]}}`` yields
        an artifact called ``order.lines``, re-attached by ``set_by_path`` under
        ``order``. Matching the whole name would call the row complete for
        ``{{$node.order.lines}}`` and answer it from a row that has only
        ``order``'s scalars.
        """
        children = [
            a for a in source_data.get("artifacts") or []
            if a.get("parent_row_key_column") and a.get("file_id")
        ]
        if not children:
            return True
        if not after_index:
            return False
        head = str(after_index[0])
        return not any(
            str(a.get("name") or "").split(".", 1)[0] == head for a in children
        )

    async def _try_odata_row_fetch(
        self,
        source_data: dict[str, Any],
        path_parts: tuple[str | int, ...],
        row_index: int,
    ) -> Any:
        """Fetch a single row via OData $skip/$top instead of downloading the full CSV.

        Results are cached per-run keyed by ``(file_id, (skip, top))`` so the
        same row referenced by multiple expressions in the same run is fetched
        only once.
        """
        if not self._row_answers_path(
            source_data, self._path_after_first_int(path_parts),
        ):
            return UNRESOLVED
        file_id = source_data.get("file_id", "")
        if not file_id:
            return UNRESOLVED

        query_params = {"$skip": str(row_index), "$top": "1"}
        cache_key = (file_id, (("$skip", query_params["$skip"]), ("$top", query_params["$top"])))
        odata_bucket = self._get_odata_cache()
        cached = odata_bucket.get(cache_key)
        if cached is not None:
            result = cached
        else:
            try:
                result = await self._file_client.query_file_data(file_id, query_params)
            except Exception as exc:
                _logger.debug(
                    "OData row fetch failed for file %s index %d, falling back to materialize: %s",
                    file_id, row_index, exc,
                )
                return UNRESOLVED
            odata_bucket[cache_key] = result

        rows = result.get("data", [])
        if not rows:
            return UNRESOLVED

        # OData returns flat CSV rows — unflatten to nested dict
        from app.layer1_domain.value_objects.value_kind import ref_uses_value_column
        from app.layer2_application.data.csv_converter import CsvConverter
        from app.layer2_application.data.data_store_service import DataStoreService
        row = CsvConverter._unflatten(rows[0], source_data.get("column_types"))

        # SA-1977 A4 — this is the FOURTH reader of a stored CSV, and the only one
        # that never learned about the reserved ``__value`` column. It serves
        # ``{{$node[i]}}``, and it WINS: the index fast-path runs before the
        # materialize fallback, so it is what every indexed reference actually
        # gets. On a ``list[scalar]`` output it handed the worker the envelope —
        #   {{$node[1]}} -> {'__row_id': 2, '__value': 20, '_row_index': 1}
        # — while ``materialize()[1]`` and ``materialize_range(ref,1,1)`` on the
        # SAME file, from the SAME OData query, both said ``20``. Three readers,
        # two answers: the exact defect this Story removes.
        if ref_uses_value_column(source_data):
            row = CsvConverter._unwrap_value(row)
        else:
            # …and the other half of the same disagreement, for the commonest
            # shape of all. Every OTHER reader strips the file service's synthetic
            # columns; this one never did, so a list-of-objects gave:
            #   materialize()[0]  -> {'sku': 'A1', 'qty': 3}
            #   {{$node[0]}}      -> {'__row_id': 1, 'sku': 'A1', 'qty': 3,
            #                         '_row_index': 0}
            # Same file, same row, two answers — and the second is the one a worker
            # actually receives, because the index fast-path wins. (Pre-existing on
            # solace; fixing only the __value half would have left the reader still
            # disagreeing with every other reader.)
            row = DataStoreService._strip_synthetic_columns(row)
            if isinstance(row, dict):
                row.pop("_row_index", None)

        # Traverse remaining path parts after the integer index
        remaining = self._path_after_first_int(path_parts)
        return self._traverse(row, remaining)

    @staticmethod
    def _find_first_int_index(path_parts: tuple[str | int, ...]) -> int | None:
        """Return the value of the first integer index in path, or None."""
        for part in path_parts:
            if isinstance(part, int):
                return part
        return None

    @staticmethod
    def _path_after_first_int(path_parts: tuple[str | int, ...]) -> tuple[str | int, ...]:
        """Return path parts after the first integer index."""
        for i, part in enumerate(path_parts):
            if isinstance(part, int):
                return path_parts[i + 1:]
        return path_parts

    # ------------------------------------------------------------------
    # Cached materialization
    # ------------------------------------------------------------------

    async def _cached_materialize(
        self, source_data: dict[str, Any], context: str = "",
    ) -> Any:
        """Materialize with a per-run cache to avoid repeated downloads.

        SA-1942 — routes through ``materialize_guarded`` so an oversized value
        raises ``MaterializeTooLargeError`` (caught + rewrapped by the caller)
        before the download. A cache hit skips the probe entirely: a value only
        lands in the cache after it materialized under the cap.

        ``context`` is the resolution EXPRESSION, and it is the whole value of
        the FULL MATERIALIZE log line. This used to pass ``file_id``, which made
        the log print the same opaque id three times — as ``node``, as
        ``expression`` and as ``file_id`` — so the one field that says WHICH
        expression caused a whole-file download said nothing. Observed live
        2026-08-21:

            cause=artifacts_not_row_rangeable node=file_01a0204d-… 
            expression=file_01a0204d-… file_id=file_01a0204d-… size_bytes=2547

        Falls back to ``file_id`` when a caller has no path, so nothing that
        used to be logged is lost.
        """
        file_id = source_data.get("file_id", "")
        bucket = self._get_materialize_cache()
        if file_id and file_id in bucket:
            return bucket[file_id]
        materialized = await self._store.materialize_guarded(
            source_data, context=context or file_id,
        )
        if file_id:
            self._remember_materialized(bucket, file_id, materialized, source_data)
        return materialized

    # ------------------------------------------------------------------
    # Meta access
    # ------------------------------------------------------------------

    def _resolve_meta(
        self,
        source_data: dict[str, Any],
        meta_key: str,
        remaining: tuple[str | int, ...],
    ) -> Any:
        """Resolve a dunder-prefixed meta key against the file ref dict."""
        if meta_key == "__file_id":
            value = source_data.get("file_id", "")
            return self._traverse(value, remaining) if remaining else value

        if meta_key == "__artifacts":
            artifacts = source_data.get("artifacts", [])
            return self._traverse(artifacts, remaining) if remaining else artifacts

        if meta_key == "__artifact":
            # Lookup by name: {{$node.__artifact.summary.csv}} → file_id
            if not remaining:
                return UNRESOLVED
            target_name = ".".join(str(p) for p in remaining)
            for art in source_data.get("artifacts", []):
                if art.get("name") == target_name:
                    return art.get("file_id", "")
            return UNRESOLVED

        if meta_key == "__content_type":
            return source_data.get("content_type", "")

        if meta_key == "__row_count":
            return source_data.get("row_count", 0)

        if meta_key == "__columns":
            columns = source_data.get("columns", [])
            return self._traverse(columns, remaining) if remaining else columns

        return UNRESOLVED

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    async def _resolve_through(
        self, data: Any, path_parts: tuple[str | int, ...],
    ) -> Any:
        """Walk *path_parts*, recursing into any nested file_ref encountered.

        SA-1905 F1 — a MERGE (or any dict output) can carry a nested file_ref
        value (``{"branchA": <file_ref>, …}``). Once ``_flatten`` preserves it
        as a pointer, resolving a deep path like
        ``{{$merge.branchA.items[0].sku}}`` must continue *into* that child
        reference — lazily (a single OData row-fetch on the child), not by
        downloading it whole. When traversal lands on a file_ref with path
        still remaining, hand the rest to ``try_resolve`` on that child.
        """
        current = data
        for i, part in enumerate(path_parts):
            if is_file_ref(current):
                return await self.try_resolve(current, path_parts[i:])
            if isinstance(part, int):
                if not isinstance(current, (list, tuple)) or part >= len(current):
                    return UNRESOLVED
                current = current[part]
            elif isinstance(current, dict):
                if part not in current:
                    return UNRESOLVED
                current = current[part]
            else:
                return UNRESOLVED
        return current

    @staticmethod
    def _traverse(data: Any, path_parts: tuple[str | int, ...]) -> Any:
        """Walk the data structure following *path_parts*."""
        current = data
        for part in path_parts:
            if isinstance(part, int):
                if not isinstance(current, (list, tuple)):
                    return UNRESOLVED
                if part >= len(current):
                    return UNRESOLVED
                current = current[part]
            elif isinstance(current, dict):
                if part not in current:
                    return UNRESOLVED
                current = current[part]
            else:
                return UNRESOLVED
        return current

"""Expression parsing, variable resolution, and condition evaluation.

Handles n8n-style expressions like ``{{$node1.data[0].name}}`` where:
- ``$node1`` references a node's output in the run context
- ``$input`` references the workflow's input data
- ``$loop`` references the current loop iteration context

``VariableResolver.resolve`` and ``ConditionEvaluator.evaluate`` are
``async def`` because resolution may materialize file-backed data via
the ``FileBackedResolverStrategy`` (which makes HTTP calls to the
file service).  Pure in-memory strategies still implement ``try_resolve``
as ``async def`` to match the protocol — they just don't await anything.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from app.layer1_domain.exceptions import WorkflowExecutionError
from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer1_domain.value_objects.csv_document import ROW_ID_COLUMN
# UNRESOLVED moved to ``resolution_scope`` (a value object) so that the
# resolution-scope module has zero service-layer dependencies. We
# re-export it here for backward compatibility — existing imports of
# the form ``from ...resolve.variable_resolver import UNRESOLVED``
# (incl. tests) keep working without code changes, and ``is UNRESOLVED``
# identity checks across modules remain valid.
from app.layer1_domain.value_objects.resolution_scope import (  # noqa: F401  re-export
    UNRESOLVED,
    ResolutionScope,
    _UnresolvedType,
    get_current_scope,
)

if TYPE_CHECKING:
    from app.layer2_application.interfaces.monitor_interface import IMonitor
    from app.layer2_application.interfaces.resolver_strategy_interface import IResolverStrategy

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Run-scope contextvar — lets resolver strategies key their caches by run_id
# without threading it through every resolve() signature.
#
# The orchestration engine sets this at the top of ``orchestrate_start`` /
# ``orchestrate_continue``; handlers invoked during that pass see the run_id
# via ``get_current_run_id()``. A contextvar is per-asyncio-task, so
# concurrent runs do not collide.
# ---------------------------------------------------------------------------

_current_run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_run_id", default=None
)


def set_current_run_id(run_id: str | None) -> contextvars.Token:
    """Set the current run_id on the active task's context. Returns the
    previous token so ``reset_current_run_id`` can restore it."""
    return _current_run_id_var.set(run_id)


def reset_current_run_id(token: contextvars.Token) -> None:
    """Restore the previous run_id contextvar value."""
    _current_run_id_var.reset(token)


def get_current_run_id() -> str | None:
    """Return the run_id associated with the current async task, if any."""
    return _current_run_id_var.get()

# ---------------------------------------------------------------------------
# Expression Parser
# ---------------------------------------------------------------------------

# Matches {{$source.path[0].key}} patterns.
# Single tokenizer truth shared with save-time validation — the grammar
# lives in layer 1 (node_spec.expression_syntax).
from app.layer1_domain.node_spec.expression_syntax import (  # noqa: E402
    EXPRESSION_PATTERN as _EXPRESSION_PATTERN,
)


@dataclass(frozen=True)
class ParsedExpression:
    raw: str
    source: str
    path_parts: tuple[str | int, ...]

    @property
    def is_empty_path(self) -> bool:
        return len(self.path_parts) == 0


def _parse_path(path_str: str) -> tuple[str | int, ...]:
    if not path_str:
        return ()
    parts: list[str | int] = []
    for token in re.split(r"\.|\[(\d+)\]", path_str):
        if token is None or token == "":
            continue
        if token.isdigit():
            parts.append(int(token))
        else:
            parts.append(token)
    return tuple(parts)


def parse_expression(expr: str) -> ParsedExpression | None:
    m = _EXPRESSION_PATTERN.fullmatch(expr.strip())
    if not m:
        return None
    source = m.group(1)
    path_str = m.group(2)
    return ParsedExpression(
        raw=expr.strip(),
        source=source,
        path_parts=_parse_path(path_str),
    )


def find_all_expressions(text: str) -> list[ParsedExpression]:
    results: list[ParsedExpression] = []
    for m in _EXPRESSION_PATTERN.finditer(text):
        source = m.group(1)
        path_str = m.group(2)
        results.append(
            ParsedExpression(
                raw=m.group(0),
                source=source,
                path_parts=_parse_path(path_str),
            )
        )
    return results


# ---------------------------------------------------------------------------
# Variable Resolution
# ---------------------------------------------------------------------------


class VariableResolutionError(WorkflowExecutionError):
    """A ``{{…}}`` expression could not be resolved.

    ``expression`` carries the exact token that failed, when known. It is set by
    ``VariableResolver.resolve`` — the only place that knows *which* of several
    expressions embedded in one string was the one that broke — and read by
    ``resolve_mapping`` to name it in the report. The message text is left
    untouched, so existing log greps and tests keep matching.
    """

    def __init__(self, message: str, expression: str | None = None) -> None:
        super().__init__(message)
        self.expression = expression


# Bounds for the failure report. A node with 200 broken keys must not build a
# 200-entry error string: that string is stored on the task row, published in a
# NodeFailed event, and rendered in the FE.
_MAX_REPORTED_FAILURES = 10
_MAX_EXPRESSION_CHARS = 120
_MAX_REASON_CHARS = 300


def _one_line(text: str, limit: int) -> str:
    """Collapse whitespace and truncate. Node bodies are multi-line templates;
    a report is only readable if each entry stays on its own line.

    ASCII only, here and in every string below. This text is formatted while
    handling an exception and then logged, persisted on the task row, and put
    on the wire — a ``UnicodeEncodeError`` raised *there* (cp1252 handlers and
    latin-1 log sinks both do it) would replace a precise diagnostic with a
    crash inside the error path.
    """
    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= limit else f"{collapsed[:limit]}..."


@dataclass(frozen=True)
class ResolutionFailure:
    """One field that did not resolve — short strings only, deliberately.

    Never holds the resolved value, the context, or the exception object:

    * a **value** here leaks into an error message that is persisted and
      republished — node input routinely carries customer data;
    * an **exception** here pins its traceback, and through it every frame's
      locals — the run context and any materialized file content with it.
      Collecting failures means holding them past the ``except`` block, which
      is exactly where that retention would become a leak.
    """

    key: str
    expression: str
    reason: str

    def __str__(self) -> str:
        return f"{self.key} <- {self.expression}: {self.reason}"


class MappingResolutionError(VariableResolutionError):
    """Every field of a mapping that failed, reported at once.

    Notification pattern (Fowler): ``resolve_mapping`` attempts every key,
    collects the failures, and raises this once — so an author with three broken
    expressions learns about three, instead of discovering them one deploy at a
    time. Subclasses ``VariableResolutionError`` so every existing
    ``except VariableResolutionError`` at the call sites keeps working.
    """

    def __init__(
        self,
        failures: "list[ResolutionFailure] | tuple[ResolutionFailure, ...]",
        total_keys: int,
        dropped: int = 0,
    ) -> None:
        self.failures = tuple(failures)
        self.total_keys = total_keys
        self.dropped = dropped
        listing = "\n".join(f"  - {failure}" for failure in self.failures)
        more = f"\n  ... and {dropped} more" if dropped else ""
        super().__init__(
            f"{len(self.failures) + dropped} of {total_keys} field(s) failed to "
            f"resolve:\n{listing}{more}"
        )


class _FailureSink:
    """Bounded collector for one ``resolve_mapping`` call.

    **Invariant: a non-empty sink always ends in a raise.** ``resolve_mapping``
    is the only thing that creates one, and it never returns a mapping when the
    sink has anything in it. Half-resolved input must not reach a worker — that
    is how a Jira comment reading "confidence {{$rootcause.metadata.score}}"
    gets posted to a customer's ticket.
    """

    __slots__ = ("failures", "dropped")

    def __init__(self) -> None:
        self.failures: list[ResolutionFailure] = []
        self.dropped = 0

    def __bool__(self) -> bool:
        return bool(self.failures) or self.dropped > 0

    def add(self, key: str, expression: str, reason: str) -> None:
        if len(self.failures) >= _MAX_REPORTED_FAILURES:
            self.dropped += 1
            return
        self.failures.append(
            ResolutionFailure(
                key=key,
                expression=_one_line(expression, _MAX_EXPRESSION_CHARS),
                reason=_one_line(reason, _MAX_REASON_CHARS),
            )
        )


def _tag_expression(exc: BaseException, raw: str) -> None:
    """Record which token failed, without touching the message.

    First writer wins: the innermost frame that knows the token is the most
    specific one. ``setattr`` rather than a plain assignment because a
    ``VariableResolutionError`` may be constructed by code that predates the
    ``expression`` field.
    """
    if getattr(exc, "expression", None) is None:
        try:
            exc.expression = raw  # type: ignore[attr-defined]
        except AttributeError:  # pragma: no cover — exotic exception types
            pass


def _failed_expression(exc: BaseException | None, value: Any) -> str:
    """The expression to name in the report.

    Prefer the token the resolver tagged onto the exception: for a template like
    ``"confidence {{$rootcause.metadata.score}}"`` that is the one broken
    expression, not the whole 400-character body the author has to search.
    """
    tagged = getattr(exc, "expression", None) if exc is not None else None
    if tagged:
        return str(tagged)
    if isinstance(value, str):
        return value
    return f"<{type(value).__name__}>"


class MetadataResolverStrategy:
    async def try_resolve(self, source_data: Any, path_parts: tuple[str | int, ...]) -> Any:
        if not isinstance(source_data, dict):
            return UNRESOLVED
        metadata = source_data.get("metadata")
        if not isinstance(metadata, dict):
            return UNRESOLVED
        extracted = metadata.get("extracted")
        if not isinstance(extracted, dict):
            return UNRESOLVED
        json_path = "$." + ".".join(
            f"[{p}]" if isinstance(p, int) else p for p in path_parts
        )
        entry = extracted.get(json_path)
        if isinstance(entry, dict) and "value" in entry and not entry.get("requires_fetch"):
            return entry["value"]
        return UNRESOLVED


# Reference fields served WITHOUT the documented ``__`` prefix.
#
# ``{{$input.file_id}}`` is the data-edit node's whole contract and appears at 39
# call sites, so it stays. Every other reference field has a dunder form
# (``__row_count``, ``__columns``, ``__content_type``, ``__artifacts`` — see
# ``FileBackedResolverStrategy._META_KEYS``), and answering it bare means a DATA
# field of the same name silently reads reference METADATA instead: with the
# file-backed strategy returning UNRESOLVED for a field the file does not have,
# ``{{$node.version}}`` returns the envelope's version string rather than
# failing. That is a wrong answer, not a slow one.
#
# SHADOW, not enforcement. Refusing the rest today would break an unknown
# customer workflow on reasoning alone; the counter below is how that decision
# gets made on tenant-1 numbers instead. Tighten to a refusal once
# ``variable_resolve_envelope_bare_read`` reads zero for a full window.
_ENVELOPE_KEYS_SERVED_BARE = frozenset({"file_id"})

# Cap on field names listed in a resolution error. A wide CSV must not turn one
# failed expression into a multi-KB exception that is then persisted on the run,
# echoed to the FE and written to the log.
_MAX_FIELDS_IN_ERROR = 25


def _missing_key_error(current: dict, part: Any) -> "VariableResolutionError":
    """The error for ``part`` missing from ``current``.

    A file_ref's KEYS are reference metadata; its FIELDS are ``columns``.
    Reporting the former is what sent a live diagnosis down the wrong path
    (tenant-1, 2026-08-11): run ``18847bb8`` failed with *"Key 'reqID' not found
    in dict. Available keys: ['__file_ref', 'arrow_file_id', … 'etag' …]"*, which
    reads like an Arrow/claim-check fault. The real cause was two nodes upstream
    — the Claude node "3. Extract reqID" returned an EMPTY object for MCKES-360
    (``size_bytes=11``, ``columns=['__row_id']``) where all 12 previous runs had
    9 columns including ``reqID``.

    The envelope already carried ``columns``, so the right answer was available
    at the point the wrong one was printed.
    """
    if not is_file_ref(current):
        return VariableResolutionError(
            f"Key '{part}' not found in dict. Available keys: {sorted(current.keys())}"
        )

    file_id = current.get("file_id", "")
    fields = sorted(
        c for c in (current.get("columns") or []) if c != ROW_ID_COLUMN
    )
    if not fields:
        return VariableResolutionError(
            f"Key '{part}' not found: the file-backed value it reads "
            f"(file_id={file_id!r}) has NO data fields: row_count="
            f"{current.get('row_count')}, columns={current.get('columns')}. "
            f"The node that produced it returned an empty value."
        )

    shown = fields[:_MAX_FIELDS_IN_ERROR]
    omitted = len(fields) - len(shown)
    listing = ", ".join(repr(f) for f in shown)
    if omitted:
        listing += f", +{omitted} more"
    return VariableResolutionError(
        f"Key '{part}' not found in the file-backed value it reads "
        f"(file_id={file_id!r}). Its fields are: [{listing}]."
    )


class TraversalResolverStrategy:
    """Last strategy in the chain — plain in-memory walk of whatever the
    file-backed and metadata strategies could not answer.

    ``monitor`` is optional so the dry-run use case can keep building this
    bare (``VariableResolver([TraversalResolverStrategy()])``).
    """

    def __init__(self, monitor: "IMonitor | None" = None) -> None:
        self._monitor = monitor

    def _check_bare_envelope(self, container: Any, key: Any) -> None:
        """Flag a reference field read off a file_ref without the ``__`` prefix.

        Called from BOTH lookup paths. The flat-key shortcut at the top of
        ``try_resolve`` answers every single-segment expression before the
        traversal loop runs, so a hook placed only in the loop would have missed
        ``{{$node.version}}`` — i.e. the exact case this counter exists to
        measure, and the counter would have read a reassuring zero.
        """
        if (
            isinstance(key, str)
            and key not in _ENVELOPE_KEYS_SERVED_BARE
            and is_file_ref(container)
        ):
            self._note_bare_envelope_read(key)

    def _note_bare_envelope_read(self, key: str) -> None:
        _logger.warning(
            "expression read reference metadata %r off a file_ref without the "
            "'__' prefix; a data field of that name would have shadowed it. "
            "Use '__%s' (see FileBackedResolverStrategy._META_KEYS).", key, key,
        )
        if self._monitor is None:
            return
        try:
            # Same label KEYS on every sample — a mismatched set is dropped
            # silently by the Prometheus client. ``key`` ranges over the
            # reference fields only (~16 fixed values), never author-chosen
            # names, so cardinality is bounded.
            self._monitor.increment(
                "variable_resolve_envelope_bare_read", tags={"key": key},
            )
        except Exception:  # noqa: BLE001 - metrics must never break resolution
            pass

    async def try_resolve(self, source_data: Any, path_parts: tuple[str | int, ...]) -> Any:
        # First, try flattened key lookup (e.g., "response_headers.x-xss-protection")
        if isinstance(source_data, dict) and path_parts:
            flat_key = ".".join(str(p) for p in path_parts)
            if flat_key in source_data:
                self._check_bare_envelope(source_data, flat_key)
                return source_data[flat_key]

        # Then, try nested traversal
        current = source_data
        for part in path_parts:
            if isinstance(part, int):
                if not isinstance(current, (list, tuple)):
                    raise VariableResolutionError(
                        f"Expected list at index {part}, got {type(current).__name__}"
                    )
                if part >= len(current):
                    raise VariableResolutionError(
                        f"Index {part} out of range (length {len(current)})"
                    )
                current = current[part]
            elif isinstance(current, dict):
                if part not in current:
                    raise _missing_key_error(current, part)
                self._check_bare_envelope(current, part)
                current = current[part]
            else:
                raise VariableResolutionError(
                    f"Cannot traverse '{part}' on {type(current).__name__}"
                )
        return current


def _short_repr(value: Any, limit: int = 200) -> str:
    """Bounded ``repr`` for info logs so materialized CSVs / large dicts
    don't flood the log. Truncates and appends the original length."""
    r = repr(value)
    return r if len(r) <= limit else f"{r[:limit]}…<{len(r)} chars>"


class VariableResolver:
    def __init__(
        self,
        strategies: list[IResolverStrategy],
        monitor: "IMonitor | None" = None,
    ) -> None:
        self._strategies = strategies
        self._monitor = monitor

    def _count_metadata_alias(self, path_parts: tuple, outcome: str) -> None:
        """Count only the ``metadata.X -> X`` rewrites — the Phase 3 gate.

        This is the evidence that decides whether the mirror can stop being
        written. While the mirror still exists the direct read wins and this
        never fires; once it does fire, a ``miss`` is a workflow that WOULD
        break, named before anything is deleted.

        The other legacy rewrites (``output.X``, ``metadata.action``) are
        excluded — they are a different, already-settled migration and counting
        them here would drown the signal.

        Both labels are passed on every call by construction (one method, one
        tag set): a Prometheus label set is frozen on first use, and a call that
        omits a key it used before is dropped silently.
        """
        if not (len(path_parts) >= 2 and path_parts[0] == "metadata"
                and path_parts[1] != "action"):
            return
        monitor = getattr(self, "_monitor", None)
        if monitor is None:
            return
        try:
            monitor.increment("metadata_alias_total", tags={"outcome": outcome})
        except Exception:  # noqa: BLE001 - never fail a resolve to count it
            _logger.debug("metadata_alias_total increment failed", exc_info=True)

    def clear_caches(self) -> None:
        """Clear per-pass caches on all strategies that support it."""
        for strategy in self._strategies:
            if hasattr(strategy, "clear_materialize_cache"):
                strategy.clear_materialize_cache()

    async def _timed_try_resolve(
        self,
        strategy: "IResolverStrategy",
        source_data: Any,
        path_parts: tuple[str | int, ...],
    ) -> Any:
        """Invoke ``strategy.try_resolve`` and record the duration as a histogram.

        Emits ``variable_resolve_duration_seconds`` tagged with the strategy
        class name and the outcome (``hit`` / ``unresolved`` / ``error``).
        Metric emission is exception-safe — all strategy exceptions propagate
        unchanged.
        """
        if self._monitor is None or not hasattr(self._monitor, "histogram"):
            # Zero-overhead fast path when no monitor is wired.
            return await strategy.try_resolve(source_data, path_parts)

        start = time.perf_counter()
        outcome = "hit"
        try:
            result = await strategy.try_resolve(source_data, path_parts)
            if result is UNRESOLVED:
                outcome = "unresolved"
            return result
        except BaseException:
            outcome = "error"
            raise
        finally:
            try:
                elapsed = time.perf_counter() - start
                self._monitor.histogram(
                    "variable_resolve_duration_seconds",
                    elapsed,
                    tags={
                        "strategy": type(strategy).__name__,
                        "outcome": outcome,
                    },
                )
            except Exception:
                _logger.info(
                    "variable_resolve_duration_seconds emit failed; swallowed",
                    exc_info=True,
                )

    async def resolve(self, expression: str, context: dict[str, Any]) -> Any:
        if not isinstance(expression, str):
            return expression
        parsed = parse_expression(expression)
        if parsed is not None:
            _logger.info(
                "resolve: full-match %r → source=%r path=%r",
                expression, parsed.source, parsed.path_parts,
            )
            try:
                return await self._resolve_single(parsed, context)
            except VariableResolutionError as exc:
                _tag_expression(exc, parsed.raw)
                raise
        expressions = find_all_expressions(expression)
        if not expressions:
            # A string that *looks* like an expression but matched no pattern
            # is returned verbatim — the silent "literal pass-through". This is
            # the #1 cause of "{{...}} not substituted": usually spaces inside
            # the braces ({{ $x }}), a ${...} delimiter, or a missing $. The
            # pattern requires {{$source.path}} with no spaces inside the braces.
            if "{{" in expression or "${" in expression:
                _logger.warning(
                    "resolve: text contains '{{'/'${' but matched NO expression "
                    "pattern — returning literal UNCHANGED: %r. Expected form is "
                    "{{$source.path}} with no spaces inside the braces.",
                    expression,
                )
            return expression
        _logger.info(
            "resolve: %d embedded expression(s) in %r: %s",
            len(expressions), expression,
            [(e.source, e.path_parts) for e in expressions],
        )
        result = expression
        for expr in expressions:
            # Tag the failure with the token that caused it. Without this, a
            # template with several expressions reports only the source+path,
            # and the author is left grepping their own node for which field
            # contained it — the exact detour that made SA-2282 take a day.
            try:
                value = await self._resolve_single(expr, context)
            except VariableResolutionError as exc:
                _tag_expression(exc, expr.raw)
                raise
            _logger.info("resolve: %r → %s", expr.raw, _short_repr(value))
            replacement = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            result = result.replace(expr.raw, replacement)
        return result

    async def resolve_schema(self, schema: Any, context: dict[str, Any]) -> Any:
        """Resolve ``{{…}}`` expressions anywhere in an FE-facing schema.

        Every string leaf containing ``{{…}}`` (field defaults, labels,
        auto-form ``controlProps`` values, …) is resolved against
        ``context``; a leaf whose expression fails to resolve is left
        unchanged. Recurses through dicts and lists; other scalars pass
        through untouched.

        This is the **single source of truth** for schema-expression
        resolution. Both the PENDING handlers at dispatch (via
        ``PendingHandlerBase._resolve_schema_expressions``) and the
        ``SchemaProvider``s at ``GET /schema`` call it, so the schema the
        FE receives from a freshly dispatched node and the one it receives
        from a design-time ``GET /schema`` resolve through identical code.
        """
        return await self._resolve_schema_node(schema, context)

    async def _resolve_schema_node(self, value: Any, context: dict[str, Any]) -> Any:
        """Recursively resolve string leaves containing ``{{…}}``."""
        if isinstance(value, str):
            if "{{" not in value:
                return value
            try:
                return await self.resolve(value, context)
            except VariableResolutionError:
                return value
        if isinstance(value, dict):
            return {
                k: await self._resolve_schema_node(v, context)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [await self._resolve_schema_node(v, context) for v in value]
        return value

    @staticmethod
    def coerce_value(value: Any, target_type: str) -> Any:
        if not target_type or target_type == "any" or value is None:
            return value
        # A file_ref is a pointer to a (possibly huge) file-backed collection
        # produced by claim-check streaming (SA-1905). For array/object targets
        # the ref STANDS IN for the collection — pass it through untouched
        # rather than trying to turn the metadata dict into a list. Never
        # materialize the rows here: that would defeat streaming and risk OOM
        # (a 120k-row output is exactly the case streaming exists for).
        # Downstream consumers (loop, len(), OData, a parent workflow) resolve
        # the ref lazily. Matches ``_resolve_value``'s file_ref pass-through.
        if target_type in ("array", "object") and is_file_ref(value):
            return value
        if target_type == "string":
            # A dict/list reaching a string-typed field was almost certainly
            # mis-declared upstream; str() would emit an unusable Python-repr
            # ([{'a': 1}], single-quoted, not valid JSON). Surface the real
            # structure instead of shredding it. ponytail: pass structured
            # values through; only scalars are str()'d here.
            if isinstance(value, (dict, list)):
                return value
            return str(value)
        if target_type == "number":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
            try:
                f = float(str(value))
                return int(f) if f == int(f) else f
            except (ValueError, TypeError):
                raise VariableResolutionError(f"Cannot coerce {value!r} to number")
        if target_type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                if value.lower() in ("true", "1", "yes"):
                    return True
                if value.lower() in ("false", "0", "no", ""):
                    return False
            if isinstance(value, (int, float)):
                return bool(value)
            raise VariableResolutionError(f"Cannot coerce {value!r} to boolean")
        if target_type == "object":
            if isinstance(value, dict):
                return value
            raise VariableResolutionError(f"Cannot coerce {value!r} to object")
        if target_type == "array":
            if isinstance(value, list):
                return value
            raise VariableResolutionError(f"Cannot coerce {value!r} to array")
        return value

    async def resolve_mapping(self, mapping: dict[str, Any], context: dict[str, Any],
                              type_map: dict[str, str] | None = None,
                              coerce_strict: bool = True) -> dict[str, Any]:
        """Resolve every value in ``mapping`` and optionally coerce by ``type_map``.

        ``coerce_strict=False`` makes coercion best-effort: a value that cannot
        be coerced to its declared type is kept as-is instead of raising. Used
        for user-supplied node input, where a field declared ``array`` may
        legitimately carry a JSON-encoded *string* that a code worker
        ``json.loads`` itself — mirrors the lenient coercion in
        ``TaskNodeHandler._apply_node_definition`` and the standalone node-test
        path, which doesn't coerce input at all.

        **Every key is attempted, and all failures are reported together** as a
        single ``MappingResolutionError``. Stopping at the first one meant an
        author with two broken expressions fixed one, re-ran, and only then
        learned about the second.

        Failing keys are *not* dropped so the good ones can proceed: a mapping
        with any failure raises, and the partially-built dict is discarded. A
        node that receives half its input either fails downstream with a
        misleading message or — worse — succeeds and ships the placeholder.
        """
        sink = _FailureSink()
        resolved = await self._resolve_mapping_into(
            mapping, context, type_map, coerce_strict, sink, ""
        )
        if sink:
            raise MappingResolutionError(sink.failures, len(mapping), sink.dropped)
        return resolved

    async def _resolve_mapping_into(
        self,
        mapping: dict[str, Any],
        context: dict[str, Any],
        type_map: dict[str, str] | None,
        coerce_strict: bool,
        sink: _FailureSink,
        prefix: str,
    ) -> dict[str, Any]:
        """Resolve every key once, routing failures to ``sink``.

        Single resolution path: ``resolve_mapping`` is a thin wrapper over this,
        so there is no "reporting variant" that could drift from the real one.
        No key is resolved twice — building the report costs no extra strategy
        call, no extra query, and no extra file materialization.
        """
        result: dict[str, Any] = {}
        for key, value in mapping.items():
            path = f"{prefix}{key}"
            try:
                resolved = await self._resolve_value(value, context, sink=sink, key=path)
            except VariableResolutionError as exc:
                sink.add(path, _failed_expression(exc, value), str(exc))
                continue
            if type_map and key in type_map:
                try:
                    resolved = self.coerce_value(resolved, type_map[key])
                except VariableResolutionError:
                    if coerce_strict:
                        # The reason is written here rather than taken from
                        # ``coerce_value``, whose message embeds a repr of the
                        # value — and this string gets persisted and published.
                        sink.add(
                            path,
                            _failed_expression(None, value),
                            f"resolved to {type(resolved).__name__}, which "
                            f"cannot be coerced to the declared type "
                            f"'{type_map[key]}'",
                        )
                        continue
            result[key] = resolved
        return result

    async def _resolve_value(
        self,
        value: Any,
        context: dict[str, Any],
        *,
        sink: "_FailureSink | None" = None,
        key: str = "",
    ) -> Any:
        """Resolve one value. ``sink`` is threaded only by ``resolve_mapping``.

        With no sink the behaviour is unchanged — first failure raises. With
        one, nested failures are reported under their own path (``body.text``,
        ``items[3]``) instead of collapsing to the top-level key.
        """
        if isinstance(value, str):
            return await self.resolve(value, context)
        if isinstance(value, dict):
            if is_file_ref(value):
                return value  # pass through file_ref, don't recurse into metadata
            if "_transform" in value:
                return await self._apply_transform(value, context)
            if sink is None:
                return await self.resolve_mapping(value, context)
            return await self._resolve_mapping_into(
                value, context, None, True, sink, f"{key}."
            )
        if isinstance(value, list):
            if sink is None:
                return [await self._resolve_value(item, context) for item in value]
            items: list[Any] = []
            for index, item in enumerate(value):
                element = f"{key}[{index}]"
                try:
                    items.append(
                        await self._resolve_value(item, context, sink=sink, key=element)
                    )
                except VariableResolutionError as exc:
                    sink.add(element, _failed_expression(exc, item), str(exc))
            return items
        return value

    async def _apply_transform(self, spec: dict[str, Any], context: dict[str, Any]) -> Any:
        """Apply a data-shape transform to a resolved source value.

        Supported transforms:
          key_value_pairs — [{key_field: "k", value_field: "v"}, ...] → {"k": "v", ...}
          pluck           — [{field: "a"}, {field: "b"}] → ["a", "b"]
          join            — ["a", "b", "c"] → "a,b,c"
          first           — [item1, item2] → item1
          flat_map        — [{k: "v1"}, {k: "v2"}] → ["v1", "v2"]
        """
        transform_type = spec["_transform"]
        source_expr = spec.get("source", "")
        source_data = await self._resolve_value(source_expr, context)

        if transform_type == "key_value_pairs":
            key_field = spec.get("key_field", "key")
            value_field = spec.get("value_field", "value")
            if not isinstance(source_data, list):
                return source_data
            return {
                item.get(key_field, ""): item.get(value_field, "")
                for item in source_data
                if isinstance(item, dict)
            }

        if transform_type == "pluck":
            field = spec.get("field", "value")
            if not isinstance(source_data, list):
                return source_data
            return [
                item.get(field, "") if isinstance(item, dict) else item
                for item in source_data
            ]

        if transform_type == "join":
            separator = spec.get("separator", ",")
            if not isinstance(source_data, list):
                return source_data
            return separator.join(str(item) for item in source_data)

        if transform_type == "first":
            if isinstance(source_data, list) and source_data:
                return source_data[0]
            return source_data

        if transform_type == "flat_map":
            field = spec.get("field", "value")
            if not isinstance(source_data, list):
                return source_data
            return [
                item.get(field, "") if isinstance(item, dict) else item
                for item in source_data
            ]

        raise VariableResolutionError(
            f"Unknown transform type: {transform_type}. "
            f"Supported: key_value_pairs, pluck, join, first, flat_map"
        )

    async def _resolve_single(
        self,
        parsed: ParsedExpression,
        context: "dict[str, Any] | ResolutionScope",
    ) -> Any:
        # ── Source lookup: ResolutionScope or legacy dict + ambient overlay ──
        #
        # Two modes are supported in lockstep so call sites don't have
        # to migrate. Today every node handler passes ``run.context.variables``
        # (a dict) — that path still wins on conflict so existing
        # workflows resolve byte-for-byte identically. The new bits
        # (``$parent``, ``$root``, ``$variable``, per-iteration
        # ``$loop`` overlay) are reachable through the ambient
        # ``ResolutionScope`` set by the engine; the resolver consults
        # it only when the dict didn't have the source.
        ambient = get_current_scope()
        if isinstance(context, ResolutionScope):
            source_data: Any = await context.lookup(parsed.source)
            if source_data is UNRESOLVED:
                source_data = None
        else:
            source_data = context.get(parsed.source) if context else None
            if source_data is None and ambient is not None:
                fallback = await ambient.lookup(parsed.source)
                if fallback is not UNRESOLVED:
                    source_data = fallback
        if source_data is None:
            available: list[str] = []
            if isinstance(context, ResolutionScope):
                available.extend(context.known_sources())
            elif context:
                available.extend(sorted(context.keys()))
            if ambient is not None:
                available.extend(ambient.known_sources())
            raise VariableResolutionError(
                f"Source '{parsed.source}' not found in context. "
                f"Available sources: {sorted(set(available))}"
            )
        # Surface the resolved source's shape so a failed *path* lookup can be
        # told apart from a failed *file materialize* (file_ref but empty/
        # un-downloadable content) — the two raise nearly identical errors.
        from app.layer1_domain.entities.data_reference import is_file_ref
        _is_ref = is_file_ref(source_data)
        _logger.info(
            "resolve: source=%r found (file_ref=%s, type=%s, file_id=%r) path=%r",
            parsed.source, _is_ref, type(source_data).__name__,
            source_data.get("file_id", "") if isinstance(source_data, dict) else "",
            parsed.path_parts,
        )
        if parsed.is_empty_path:
            return source_data
        # 1. Try original path with all strategies
        original_error: VariableResolutionError | None = None
        for strategy in self._strategies:
            try:
                result = await self._timed_try_resolve(strategy, source_data, parsed.path_parts)
                if result is not UNRESOLVED:
                    _logger.info(
                        "resolve: source=%r path=%r resolved by %s → %s",
                        parsed.source, parsed.path_parts,
                        type(strategy).__name__, _short_repr(result),
                    )
                    return result
            except VariableResolutionError as exc:
                # Remember first error; fallback may still succeed.
                if original_error is None:
                    original_error = exc
        # 2. Backward-compat fallback: rewrite legacy INPUT/HUMAN_ACTION paths
        rewritten = _rewrite_legacy_path(parsed.path_parts)
        if rewritten is not None:
            for strategy in self._strategies:
                try:
                    result = await self._timed_try_resolve(strategy, source_data, rewritten)
                    if result is not UNRESOLVED:
                        self._count_metadata_alias(parsed.path_parts, "hit")
                        return result
                except VariableResolutionError:
                    continue
            self._count_metadata_alias(parsed.path_parts, "miss")
        # If the source was a file_ref but no strategy resolved the path, the
        # likely cause is that the file content didn't materialize (empty file,
        # un-downloadable file_id, upload swallowed upstream) — resolution then
        # falls through to the file_ref envelope, whose keys (__file_ref,
        # file_id, …) surface in the error below. This is the signature the
        # removed ``extracted`` scalar cache used to mask.
        if _is_ref:
            # Surface the envelope's *support scope* (the columns/keys that
            # actually materialized and could resolve) and its *file ref*
            # identity (file_id/artifact_id + nested artifacts) so the
            # not-reproducible-locally case (e.g. a child ``$input`` file_ref
            # missing ``templateId``) can be pinned from the failing env's logs.
            _artifact_ids = [
                (a.get("artifact_id"), a.get("file_id"))
                for a in (source_data.get("artifacts") or [])
                if isinstance(a, dict)
            ]
            _logger.warning(
                "resolve: source=%r is a file_ref but path %r did NOT resolve "
                "from materialized content — the file may be empty or "
                "un-downloadable. Falling through to the file_ref envelope. "
                "support_scope(columns)=%r file_ref(file_id=%r artifact_id=%r "
                "row_count=%r version=%r etag=%r nested_artifacts=%s)",
                parsed.source, parsed.path_parts,
                source_data.get("columns"),
                source_data.get("file_id", ""),
                source_data.get("artifact_id", ""),
                source_data.get("row_count"),
                source_data.get("version"),
                source_data.get("etag", ""),
                _short_repr(_artifact_ids),
            )
        if original_error is not None:
            raise original_error
        available_keys = sorted(source_data.keys()) if isinstance(source_data, dict) else []
        raise VariableResolutionError(
            f"No strategy could resolve path {parsed.path_parts!r} "
            f"on source '{parsed.source}'. "
            f"Available keys at root: {available_keys}"
        )


def _rewrite_legacy_path(
    path_parts: tuple[str | int, ...],
) -> tuple[str | int, ...] | None:
    """Rewrite legacy INPUT/HUMAN_ACTION wrapping paths for backward compat.

    Before file-backed storage, INPUT/HUMAN_ACTION nodes wrapped submissions
    as ``{"output": submitted_data, "metadata": {"type": ..., "action": ...}}``.
    That wrapping was removed; expressions using the old paths are rewritten
    here so existing workflows keep working.

    Mappings:
        ``output.X...``         → ``X...``
        ``metadata.action``     → ``_action``
        ``metadata.X...``       → ``X...``   (see below)

    Returns ``None`` when no rewrite applies (so the caller can stop trying).
    """
    if not path_parts:
        return None
    first = path_parts[0]
    if first == "output" and len(path_parts) > 1:
        return path_parts[1:]
    if first == "metadata" and len(path_parts) >= 2 and path_parts[1] == "action":
        return ("_action",)
    # A TASK node's ``metadata`` IS its output: ``complete_task_use_case``
    # stores the worker's result under that key AND spreads the same keys at the
    # top level, so ``$node.metadata.issue`` and ``$node.issue`` have always been
    # the same value. Measured 2026-08-19, the duplicate is exactly 50% of a real
    # node output — twice in the CSV tree, twice in the Arrow sidecar, twice in
    # HANA. This alias is what lets the write be dropped without breaking the 15
    # tenant-1 workflows (69 expressions) that read the ``metadata`` spelling.
    #
    # Deliberately UNREACHABLE today: this function only runs after every
    # strategy has already failed on the direct path (see the caller), so while
    # the mirror is still written the direct read wins and nothing changes. It
    # becomes the safety net the moment the mirror stops being written — ship it
    # first, watch ``metadata_alias_total``, flip the write last.
    #
    # A BARE ``metadata`` is deliberately not rewritten: rewriting it to the
    # empty path would resolve to the node's entire output, which is a silent
    # wrong answer rather than an error. 0 of 100 tenant-1 workflows read it.
    if first == "metadata" and len(path_parts) >= 2:
        return path_parts[1:]
    return None


# ---------------------------------------------------------------------------
# Condition Evaluator
# ---------------------------------------------------------------------------

_CONDITION_OP_PATTERN = re.compile(
    r"^(.+?)\s+(==|!=|>=|<=|>|<|IN|CONTAINS)\s+(.+)$", re.IGNORECASE
)


# Operators whose meaning over a COLLECTION is membership. Only these are
# pushed down; see ``_try_collection_membership``.
_MEMBERSHIP_OPS = frozenset({"CONTAINS", "IN"})

# Imported lazily-by-name to keep the dependency one-way: collection_membership
# depends on odata_predicate and layer1 only, never back on this module.
from app.layer2_application.resolve.collection_membership import (  # noqa: E402
    UNKNOWN as _MEMBERSHIP_UNKNOWN,
)


class ConditionEvaluator:
    def __init__(
        self,
        resolver: VariableResolver,
        membership_probe: Any = None,
    ) -> None:
        self._resolver = resolver
        # SA-2273 — answers "does this file-backed collection contain x" at the
        # file service. Optional: without it the evaluator behaves exactly as
        # before, so every existing construction site keeps working.
        self._membership_probe = membership_probe

    async def evaluate(self, condition_expr: str, context: dict[str, Any]) -> bool:
        if not condition_expr or not condition_expr.strip():
            return True
        expr = condition_expr.strip()
        if " OR " in expr:
            parts = expr.split(" OR ")
            for p in parts:
                if await self.evaluate(p.strip(), context):
                    return True
            return False
        if " AND " in expr:
            parts = expr.split(" AND ")
            for p in parts:
                if not await self.evaluate(p.strip(), context):
                    return False
            return True
        return await self._evaluate_single(expr, context)

    async def _try_collection_membership(
        self, expr: str, context: dict[str, Any],
    ) -> Any:
        """Answer ``<collection> CONTAINS x`` / ``x IN <collection>`` at the
        file service, or return ``UNRESOLVED`` to leave the old path alone.

        Runs BEFORE the normal path and matches the operator on the RAW
        expression, because the defect it fixes is created by resolution
        itself: interpolating a file_ref into the expression string turns the
        pointer into text, after which the comparison runs against the
        reference ENVELOPE instead of the data (SA-2273).

        Deliberately narrow. Only membership operators, and only when an
        operand really resolves to a file_ref; anything else — including a
        collection compared with ``==`` — falls through untouched. For those,
        inline and file-backed already agree (both serialise to something that
        does not equal a scalar), so there is nothing to fix and no reason to
        take the risk.
        """
        if self._membership_probe is None:
            return UNRESOLVED
        m = _CONDITION_OP_PATTERN.match(expr)
        if not m:
            return UNRESOLVED
        op = m.group(2).upper()
        if op not in _MEMBERSHIP_OPS:
            return UNRESOLVED

        # CONTAINS puts the collection on the left, IN puts it on the right.
        haystack_raw, needle_raw = (
            (m.group(1), m.group(3)) if op == "CONTAINS"
            else (m.group(3), m.group(1))
        )
        try:
            haystack = await self._resolver.resolve(haystack_raw.strip(), context)
        except Exception:
            return UNRESOLVED
        if not is_file_ref(haystack):
            return UNRESOLVED

        try:
            needle = await self._resolver.resolve(needle_raw.strip(), context)
        except Exception:
            return UNRESOLVED
        if isinstance(needle, str):
            needle = _coerce(needle)

        answer = await self._membership_probe.contains(haystack, needle)
        return UNRESOLVED if answer is _MEMBERSHIP_UNKNOWN else answer

    async def _evaluate_single(self, expr: str, context: dict[str, Any]) -> bool:
        membership = await self._try_collection_membership(expr, context)
        if membership is not UNRESOLVED:
            return bool(membership)

        resolved = await self._resolver.resolve(expr, context)
        if isinstance(resolved, bool):
            return resolved
        # SA-1905 D2 — a condition on a whole file-backed value
        # ({{$node.items}}) resolves to a file_ref dict. bool(non-empty
        # dict) was always True, so an EMPTY collection wrongly took the
        # true branch. Interpret truthiness as "has data" (rows / nested
        # artifacts / columns) — cheap, no download.
        if is_file_ref(resolved):
            return bool(
                (resolved.get("row_count") or 0)
                or resolved.get("artifacts")
                or resolved.get("columns")
            )
        if not isinstance(resolved, str):
            return bool(resolved)
        resolved_str = str(resolved)
        m = _CONDITION_OP_PATTERN.match(resolved_str)
        if not m:
            return bool(_coerce(resolved_str))
        left_raw = m.group(1).strip()
        op = m.group(2).upper()
        right_raw = m.group(3).strip()
        left = _coerce(left_raw)
        right = _coerce(right_raw)
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == ">":
            return _numeric(left) > _numeric(right)
        if op == ">=":
            return _numeric(left) >= _numeric(right)
        if op == "<":
            return _numeric(left) < _numeric(right)
        if op == "<=":
            return _numeric(left) <= _numeric(right)
        if op == "IN":
            if isinstance(right, str):
                return str(left) in right
            return left in right
        if op == "CONTAINS":
            if isinstance(left, str):
                return str(right) in left
            return right in left
        return False


def _coerce(value: str) -> Any:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower == "null" or lower == "none":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _numeric(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (ValueError, TypeError):
        raise VariableResolutionError(
            f"Cannot compare non-numeric value: {value!r}"
        )

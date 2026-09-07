"""The one place a rule expression is turned into a polars expression.

Two jobs, and they do not pull against each other.

**Reach.** `expression` is the rule type that covers whatever the declarative
types do not, so it has to accept whatever a rule author actually writes. This
module accepts the canonical form (``pl.col('x') > 5``), the bare form
(``col('x') > 5``), other people's import habits (``F.col``, ``polars.col``),
frame-style prototypes (``df['x'] > 5``), lambdas, comprehensions, f-strings,
multi-line bodies with intermediate variables, pasted markdown fences, stray
``import polars as pl`` headers, and — when the text is not Python at all —
SQL, via ``pl.sql_expr``. Nearly the whole polars surface is in scope, bare and
namespaced.

**Safety.** What is refused is not expression syntax; it is Python's escape
hatches. `pl.__loader__.__init__.__globals__['__builtins__']['open']` walks from
a module object back to real builtins, and every step of that walk goes through
a private attribute. No rule needs one. Likewise the ``map_*`` family, which
takes an arbitrary Python callable, and polars' own file I/O (``read_*``,
``scan_*``, ``write_*``, ``sink_*``), which would otherwise be reachable through
the ``pl`` name.

The cost of closing that is five expression methods and the I/O namespace.
Everything a validation or mapping rule would legitimately say still compiles.
"""

from __future__ import annotations

import ast
import builtins
import logging
import re
import types
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Set

import polars as pl

try:  # polars >= 0.19
    import polars.selectors as _polars_selectors
except Exception:  # pragma: no cover - very old polars
    _polars_selectors = None

logger = logging.getLogger(__name__)


class RuleExpressionError(ValueError):
    """A rule expression that cannot be compiled, or must not be."""


# --------------------------------------------------------------------------
# What is refused
# --------------------------------------------------------------------------

#: Take an arbitrary Python callable, i.e. code execution wearing another hat.
CALLABLE_ATTRS = frozenset(
    {
        "apply",
        "map",
        "map_alias",
        "map_batches",
        "map_elements",
        "map_groups",
        "map_rows",
        "pipe",
        "rolling_map",
        "register_plugin",
        "register_plugin_function",
    }
)

#: Reach the filesystem or a live SQL context through the ``pl`` name.
IO_ATTRS = frozenset({"SQLContext", "Config", "deserialize", "serialize"})

#: polars names file I/O uniformly, so a prefix ban is exact here.
IO_PREFIXES = ("read_", "scan_", "write_", "sink_")

BANNED_ATTRS = CALLABLE_ATTRS | IO_ATTRS


def _is_banned(name: str) -> bool:
    return name in BANNED_ATTRS or name.startswith(IO_PREFIXES)


#: Builtins a rule author has a real use for. Deliberately values, not the
#: module — there is no route from here back to ``__import__`` or ``open``.
SAFE_BUILTINS: Dict[str, Any] = {
    name: getattr(builtins, name)
    for name in (
        "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
        "float", "format", "frozenset", "int", "len", "list", "map", "max",
        "min", "range", "reversed", "round", "set", "sorted", "str", "sum",
        "tuple", "zip",
    )
    if hasattr(builtins, name)
}
# ``map``/``filter`` above are the builtins, which only ever see values the
# expression already had; they are not the banned polars ``map`` attribute.

# --------------------------------------------------------------------------
# What is allowed
# --------------------------------------------------------------------------

_ALLOWED_NODES = (
    ast.Expression,
    ast.Module,
    # values
    ast.Constant, ast.Name, ast.Load, ast.Store, ast.Attribute, ast.Call,
    ast.keyword, ast.Starred,
    ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.Subscript, ast.Slice,
    ast.JoinedStr, ast.FormattedValue,
    # control-ish forms that stay expressions
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Lambda,
    ast.arguments, ast.arg,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension,
    # statements, for the multi-line dialect
    ast.Expr, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Return, ast.Pass,
    # operators
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.LShift, ast.RShift, ast.BitAnd, ast.BitOr, ast.BitXor, ast.MatMult,
    ast.Invert, ast.USub, ast.UAdd, ast.Not,
    ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Is, ast.IsNot,
)

if hasattr(ast, "Index"):  # removed in 3.9, still emitted by older parsers
    _ALLOWED_NODES = _ALLOWED_NODES + (ast.Index,)  # type: ignore[assignment]

#: Import habits from other prototypes, all pointing at the same module.
_MODULE_ALIASES = ("pl", "polars", "F", "PL", "P", "p")

_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_+-]*\s*$|^\s*```\s*$", re.MULTILINE)
_IMPORT_LINE = re.compile(
    r"^[ \t]*(?:import[ \t]+(?P<mod>[\w.]+)|from[ \t]+(?P<from>[\w.]+)[ \t]+import)[^\n]*$",
    re.MULTILINE,
)
_DIALECT_PREFIX = re.compile(r"^\s*(sql|python|polars|expr)\s*:\s*", re.IGNORECASE)
_RETURN_LINE = re.compile(r"^([ 	]*)return[ 	]+", re.MULTILINE)

#: Where a multi-line body leaves its value.
RESULT_NAME = "__rule_result__"


def _polars_scope() -> Dict[str, Any]:
    """Every public polars name, bare. Built once, then copied per call.

    Modules are dropped, and that is not tidiness. ``polars/__init__.py`` does
    ``import os``, so a plain ``{"pl": pl}`` scope hands out ``pl.os.system``
    with no dunder walking needed at all — a shorter escape than the one this
    module was written for.
    """
    scope: Dict[str, Any] = {}
    for name in dir(pl):
        if name.startswith("_") or _is_banned(name):
            continue
        try:
            value = getattr(pl, name)
        except Exception:  # pragma: no cover - defensive
            continue
        if isinstance(value, types.ModuleType):
            continue
        scope[name] = value
    return scope


_POLARS_SCOPE = _polars_scope()

#: What the ``pl`` name resolves to. Not the module — the module is a doorway.
_POLARS_FACADE = types.SimpleNamespace(**_POLARS_SCOPE)
if _polars_selectors is not None:
    _POLARS_FACADE.selectors = _polars_selectors


def build_scope(
    columns: Optional[Sequence[str]] = None,
    frame: Any = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Names a rule expression may use.

    Layered deliberately: polars first, then Python builtins on top (so a bare
    ``sum``/``all``/``min`` means what it means in Python — the polars ones stay
    reachable as ``pl.sum`` etc.), then the rule-specific handles.
    """
    scope: Dict[str, Any] = dict(_POLARS_SCOPE)
    scope.update(SAFE_BUILTINS)
    for alias in _MODULE_ALIASES:
        scope[alias] = _POLARS_FACADE
    if _polars_selectors is not None:
        scope.setdefault("cs", _polars_selectors)
        scope.setdefault("selectors", _polars_selectors)
    scope["col"] = pl.col
    scope["lit"] = pl.lit
    scope["cols"] = list(columns or [])
    scope["columns"] = scope["cols"]
    if frame is not None:
        scope["df"] = frame
        scope["frame"] = frame
        scope["data"] = frame
    if extra:
        scope.update(extra)
    return scope


def describe_scope(columns: Optional[Sequence[str]] = None) -> str:
    """Used in error messages, so a rejected rule says what it could have said."""
    handles = ["pl", "col", "lit", "cols", "df", "cs"]
    return (
        f"available: {', '.join(handles)} plus the polars namespace and safe "
        f"builtins; columns in scope: {list(columns or [])}"
    )


# --------------------------------------------------------------------------
# Dialect normalisation
# --------------------------------------------------------------------------


def _strip_polars_imports(source: str) -> str:
    """Drop a pasted snippet's polars import header; refuse any other import.

    The header is noise — the module is already in scope. Silently dropping
    *every* import would be worse than noise: `import os` would vanish and
    `os.getcwd()` would then be judged on whether `os` happens to be a known
    name, which is not a question a guard should be asking.
    """
    def _replace(match: "re.Match[str]") -> str:
        module = match.group("mod") or match.group("from") or ""
        root = module.split(".")[0]
        if root in {"polars", "pl"}:
            return ""
        raise RuleExpressionError(
            f"a rule expression may not import '{module}'; polars is already "
            "in scope and nothing else is available"
        )

    return _IMPORT_LINE.sub(_replace, source)

def _normalise(text: str) -> tuple[str, Optional[str]]:
    """Strip the wrapping people paste, and pull out an explicit dialect tag."""
    if not isinstance(text, str):
        raise RuleExpressionError(
            f"expression must be a string, got {type(text).__name__}"
        )
    source = text.replace("\r\n", "\n").replace("\r", "\n")
    source = _FENCE.sub("", source)

    dialect = None
    match = _DIALECT_PREFIX.match(source)
    if match:
        dialect = match.group(1).lower()
        source = source[match.end():]

    source = _strip_polars_imports(source)

    # `return x` is a syntax error outside a function, but it is how a lot
    # of people write the last line of a snippet.
    source = _RETURN_LINE.sub(lambda m: m.group(1) + RESULT_NAME + " = ", source)

    source = source.strip()
    while source.endswith(";"):
        source = source[:-1].rstrip()
    if not source:
        raise RuleExpressionError("expression is empty")
    return source, dialect


def _parse(source: str) -> tuple[ast.AST, Set[str]]:
    """Parse as an expression; fall back to a multi-line body.

    Returns the tree plus the names the body binds, so the guard does not
    reject a rule's own intermediate variables.
    """
    try:
        return ast.parse(source, mode="eval"), set()
    except SyntaxError:
        pass

    try:
        module = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise RuleExpressionError(
            f"not a valid expression: {exc.msg} (line {exc.lineno})"
        ) from exc

    if not module.body:
        raise RuleExpressionError("expression is empty")

    bound: Set[str] = set()
    for statement in module.body:
        for target in _assignment_targets(statement):
            bound.add(target)

    last = module.body[-1]
    if isinstance(last, ast.Expr):
        produced = last.value
    elif isinstance(last, (ast.Assign, ast.AnnAssign)) and last.value is not None:
        # `expr = pl.col('x') > 5` with nothing after it: take the assignment.
        produced = last.value
    else:
        raise RuleExpressionError(
            "a multi-line expression must end with the value it produces "
            "(an expression, a `return`, or an assignment)"
        )

    # `exec` discards the value of a trailing expression, so name it.
    module.body.append(
        ast.Assign(
            targets=[ast.Name(id=RESULT_NAME, ctx=ast.Store())],
            value=produced,
        )
    )
    bound.add(RESULT_NAME)
    module = ast.fix_missing_locations(module)
    return module, bound


def _assignment_targets(node: ast.AST) -> Iterable[str]:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            yield from _names_in_target(target)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        yield from _names_in_target(node.target)


def _names_in_target(target: ast.AST) -> Iterable[str]:
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _names_in_target(element)
    elif isinstance(target, ast.Starred):
        yield from _names_in_target(target.value)


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------

class _Guard(ast.NodeVisitor):
    """Scope-aware walk: allows a rule's own locals, refuses the escapes."""

    def __init__(self, allowed_names: Set[str]) -> None:
        self._scopes = [set(allowed_names)]
        self._rebound: Set[str] = set()

    # -- scope bookkeeping ------------------------------------------------
    def _known(self, name: str) -> bool:
        return any(name in scope for scope in self._scopes)

    def _bind(self, name: str) -> None:
        self._scopes[-1].add(name)
        self._rebound.add(name)

    def _push(self) -> None:
        self._scopes.append(set())

    def _pop(self) -> None:
        self._scopes.pop()

    # -- the three refusals ----------------------------------------------
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            # `pl.x = 1` mutates the shared polars namespace for every later
            # rule in the process. A rule may compute, not modify.
            raise RuleExpressionError(
                f"a rule expression may not assign to '{node.attr}'"
            )
        if node.attr.startswith("_"):
            # The whole escape lives here: __class__, __globals__, __loader__…
            raise RuleExpressionError(
                f"attribute '{node.attr}' is not allowed — private attributes are "
                "how sandbox escapes are built, and no rule needs one"
            )
        if node.attr in CALLABLE_ATTRS:
            raise RuleExpressionError(
                f"'{node.attr}' is not allowed because it runs an arbitrary Python "
                "callable; express the logic with polars operators instead"
            )
        if node.attr in IO_ATTRS or node.attr.startswith(IO_PREFIXES):
            raise RuleExpressionError(
                f"'{node.attr}' is not allowed — a rule may not touch files or "
                "open a SQL context"
            )
        self._check_polars_attribute(node)
        self.generic_visit(node)

    def _check_polars_attribute(self, node: ast.Attribute) -> None:
        """`pl.<x>` must be a real polars export, resolved now rather than later.

        Without this the refusal is an AttributeError at evaluation time, which
        means the request-boundary check would wave `pl.os.system(...)` through
        and only the run would fail.
        """
        base = node.value
        if not isinstance(base, ast.Name):
            return
        if base.id not in _MODULE_ALIASES or base.id in self._rebound:
            return
        if not hasattr(_POLARS_FACADE, node.attr):
            raise RuleExpressionError(
                f"'{base.id}.{node.attr}' is not a polars export. polars imports "
                "modules of its own (os, io, contextlib…) and they are not "
                "reachable from a rule"
            )

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            raise RuleExpressionError(
                "a rule expression may not assign into a container"
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._bind(node.id)
            return
        if not self._known(node.id):
            raise RuleExpressionError(
                f"name '{node.id}' is not available in a rule expression"
            )

    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, _ALLOWED_NODES):
            raise RuleExpressionError(
                f"{type(node).__name__} is not allowed in a rule expression"
            )
        super().generic_visit(node)

    # -- forms that introduce their own names -----------------------------
    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._push()
        try:
            for argument in _lambda_args(node.args):
                self._bind(argument)
            for default in list(node.args.defaults) + [
                d for d in node.args.kw_defaults if d is not None
            ]:
                self.visit(default)
            self.visit(node.body)
        finally:
            self._pop()

    def _visit_comprehension(self, node: ast.AST, parts: Sequence[ast.AST]) -> None:
        self._push()
        try:
            for generator in node.generators:  # type: ignore[attr-defined]
                self.visit(generator.iter)
                for name in _names_in_target(generator.target):
                    self._bind(name)
                for condition in generator.ifs:
                    self.visit(condition)
            for part in parts:
                self.visit(part)
        finally:
            self._pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node, [node.elt])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node, [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node, [node.key, node.value])


def _lambda_args(arguments: ast.arguments) -> Iterable[str]:
    for group in ("posonlyargs", "args", "kwonlyargs"):
        for argument in getattr(arguments, group, []) or []:
            yield argument.arg
    for slot in ("vararg", "kwarg"):
        argument = getattr(arguments, slot, None)
        if argument is not None:
            yield argument.arg


# --------------------------------------------------------------------------
# Compile / evaluate
# --------------------------------------------------------------------------


#: Naming any of these means the author is writing polars, not SQL.
_POLARS_HANDLES = frozenset(_MODULE_ALIASES) | {
    "col", "lit", "cols", "columns", "df", "frame", "data", "cs", "selectors",
}


def _uses_polars_handle(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id in _POLARS_HANDLES
        for node in ast.walk(tree)
    )


def compile_expression(
    text: str,
    columns: Optional[Sequence[str]] = None,
    frame: Any = None,
    extra: Optional[Mapping[str, Any]] = None,
):
    """Check and compile, returning ``(code, scope, dialect)``."""
    source, dialect = _normalise(text)
    scope = build_scope(columns=columns, frame=frame, extra=extra)

    if dialect == "sql":
        return _sql_code(source), scope, "sql"

    try:
        tree, bound = _parse(source)
    except RuleExpressionError:
        # Not Python at all. A rule written in SQL is a legitimate prototype,
        # so try that before giving up.
        if _try_sql(source) is not None:
            return _sql_code(source), scope, "sql"
        raise

    try:
        _Guard(set(scope) | bound | {RESULT_NAME}).visit(tree)
    except RuleExpressionError:
        # `qty > 1` is valid Python *and* valid SQL, so it parses and then
        # fails on an unbound name. Reinterpret it as SQL only when the text
        # never reaches for a polars handle — that way a genuine typo in
        # `pl.col('x') > qtyy` still reports the typo instead of being
        # silently re-read as something else.
        if not _uses_polars_handle(tree) and _try_sql(source) is not None:
            return _sql_code(source), scope, "sql"
        raise

    mode = "eval" if isinstance(tree, ast.Expression) else "exec"
    return compile(tree, "<rule-expression>", mode), scope, mode


def _sql_code(source: str):
    return ("__sql__", source)


def _try_sql(source: str) -> Any:
    try:
        return pl.sql_expr(source)
    except Exception:
        return None


def evaluate_expression(
    text: str,
    columns: Optional[Sequence[str]] = None,
    frame: Any = None,
    extra: Optional[Mapping[str, Any]] = None,
    expect: str = "expr",
) -> Any:
    """Compile and run a rule expression.

    ``expect="expr"`` coerces the result to a ``pl.Expr`` — a Series, a literal,
    or a lambda over the frame all become one, so a rule written in any of the
    accepted prototypes lands in the same place. ``expect="any"`` returns the
    value untouched, for callers that want a frame back.
    """
    code, scope, mode = compile_expression(text, columns=columns, frame=frame, extra=extra)

    if isinstance(code, tuple) and code and code[0] == "__sql__":
        try:
            value = pl.sql_expr(code[1])
        except Exception as exc:
            raise RuleExpressionError(f"invalid SQL expression: {exc}") from exc
    else:
        try:
            value = eval(code, {"__builtins__": {}}, scope)  # noqa: S307 - guarded above
            if mode == "exec":
                value = scope.get("__rule_result__", value)
        except RuleExpressionError:
            raise
        except Exception as exc:
            raise RuleExpressionError(
                f"expression failed to evaluate: {type(exc).__name__}: {exc}"
            ) from exc

    if expect == "any":
        return value
    return coerce_to_expr(value, frame=frame, source=text)


def coerce_to_expr(value: Any, frame: Any = None, source: str = "") -> pl.Expr:
    """Meet the rule author where they wrote it."""
    if isinstance(value, pl.Expr):
        return value
    if isinstance(value, pl.Series):
        return pl.lit(value)
    if callable(value) and not isinstance(value, type):
        # `lambda df: df['x'] > 5` — a shape people bring from pandas.
        if frame is None:
            raise RuleExpressionError(
                "expression is a function but there is no frame to apply it to; "
                "write it as a polars expression instead"
            )
        return coerce_to_expr(value(frame), frame=None, source=source)
    if isinstance(value, (bool, int, float, str)):
        return pl.lit(value)
    if isinstance(value, (pl.DataFrame, pl.LazyFrame)):
        raise RuleExpressionError(
            "expression produced a DataFrame, but a rule needs a single "
            "expression (something like pl.col('x') > 5)"
        )
    if value is None:
        raise RuleExpressionError("expression produced None")
    raise RuleExpressionError(
        f"expression produced {type(value).__name__}, which is not a polars expression"
    )


def evaluate_predicate(
    text: str,
    columns: Optional[Sequence[str]] = None,
    frame: Any = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> pl.Expr:
    """A rule expression used as a condition."""
    return evaluate_expression(text, columns=columns, frame=frame, extra=extra)


def is_safe(text: str, columns: Optional[Sequence[str]] = None) -> bool:
    """Check without running — used by the request-boundary validators."""
    try:
        compile_expression(text, columns=columns)
        return True
    except RuleExpressionError:
        return False

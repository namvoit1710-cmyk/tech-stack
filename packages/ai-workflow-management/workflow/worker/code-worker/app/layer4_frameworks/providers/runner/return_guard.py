"""SA-1952 — the code node must RETURN its value, and say so when it doesn't.

Both runners wrap the user's script in a function body, so a top-level ``return``
is legal and is how the node's output is produced. A body with **no return at
all** therefore falls off the end and yields ``None`` / ``undefined`` — and both
runners then reported that as a **success** with an empty output. The node went
green, the empty value flowed downstream, and the first visible symptom was a
``Key 'sku' not found`` several nodes later, pointing the user at the wrong node.

``ok: True`` must mean *the user's intent was executed*, not *the interpreter did
not throw*.

**Why this cannot produce a false positive.** The check is "does the body contain
a return statement at all?" — not "does it always return". A body with no return
*always* yields None/undefined, so refusing it is never wrong. A *conditional*
return (which may legitimately yield None on some branch) still contains a return
and is left completely alone. The failure mode of the check is a false *negative*
(a return we failed to see), which simply degrades to today's behaviour.

The convention is shared between the two runners, so the guard is too.
"""

from __future__ import annotations

import ast
import re

# Names a user is likely to assign to, believing it becomes the node output.
# Used only to sharpen the error message.
_SINK_NAMES = ("result", "output", "data", "rows", "items")


class CodeNodeNoReturn(Exception):
    """The user's code never returns a value, so the node output would be empty."""


def _message(sinks: list[str]) -> str:
    lines = [
        "your code never returns a value, so this node's output would be empty.",
        "The code node runs your script as a function body: `return` the value you "
        "want this node to output.",
    ]
    if sinks:
        name = sinks[0]
        lines.append(
            f"You assigned `{name}` but never returned it — did you mean `return {name}`?"
        )
    else:
        lines.append(
            "Assigning to `result` / `output` / `data` has no effect on the node output."
        )
    return " ".join(lines)


# ---------------------------------------------------------------------------
# Python — a real AST walk
# ---------------------------------------------------------------------------

def _is_inner_scope(node: ast.AST) -> bool:
    """A ``return`` inside a nested function is that function's, not the node's."""
    return isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
    )


def _contains_return(node: ast.AST) -> bool:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Return):
            return True
        if _is_inner_scope(child):
            continue  # a helper's return does not make the node return
        if _contains_return(child):
            return True
    return False


def _assigned_sinks(fn: ast.AST) -> list[str]:
    found: list[str] = []
    for stmt in getattr(fn, "body", []):
        targets: list[ast.AST] = []
        if isinstance(stmt, ast.Assign):
            targets = list(stmt.targets)
        elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
            targets = [stmt.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id in _SINK_NAMES:
                found.append(target.id)
    return found


def ensure_python_returns(wrapper_src: str, func_name: str = "__user_main") -> None:
    """Raise :class:`CodeNodeNoReturn` when the wrapped user body has no ``return``.

    *wrapper_src* is the already-wrapped source (the user's code indented into
    ``def __user_main(...)``), because a bare top-level ``return`` is not parseable
    as a module on its own.
    """
    tree = ast.parse(wrapper_src)
    fn = next(
        (
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == func_name
        ),
        None,
    )
    if fn is None:  # pragma: no cover - the wrapper always defines it
        return
    if _contains_return(fn):
        return
    raise CodeNodeNoReturn(_message(_assigned_sinks(fn)))


# ---------------------------------------------------------------------------
# JavaScript — no AST available in-process, so a deliberately conservative scan
# ---------------------------------------------------------------------------

# Comments first, then string literals. Either order mis-handles the pathological
# case (a quote inside a comment, or `//` inside a string); that only ever makes
# us MISS a return, which degrades to the old silent-null behaviour rather than
# rejecting valid code.
_JS_STRIPPERS = (
    (re.compile(r"/\*.*?\*/", re.S), " "),
    (re.compile(r"//[^\n]*"), " "),
    (re.compile(r"'(?:\\.|[^'\\\n])*'"), "''"),
    (re.compile(r'"(?:\\.|[^"\\\n])*"'), '""'),
    (re.compile(r"`(?:\\.|[^`\\])*`", re.S), "``"),
)

_JS_SINK_ASSIGN = re.compile(
    r"^\s*(?:var|let|const)?\s*(" + "|".join(_SINK_NAMES) + r")\s*=(?!=)",
    re.M,
)


def _strip_js(src: str) -> str:
    for pattern, replacement in _JS_STRIPPERS:
        src = pattern.sub(replacement, src)
    return src


def js_has_return(src: str) -> bool:
    return bool(re.search(r"\breturn\b", _strip_js(src)))


def ensure_js_returns(src: str) -> None:
    """Raise :class:`CodeNodeNoReturn` when the user's JS body has no ``return``."""
    if js_has_return(src):
        return
    stripped = _strip_js(src)
    sinks = [m.group(1) for m in _JS_SINK_ASSIGN.finditer(stripped)]
    raise CodeNodeNoReturn(_message(sinks))

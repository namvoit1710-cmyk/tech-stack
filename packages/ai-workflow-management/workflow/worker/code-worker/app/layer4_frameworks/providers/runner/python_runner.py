"""In-process Python script execution for the runner child process.

This module is imported and called *inside* the isolated runner subprocess —
never from the worker's main process. It restricts the builtins available to
the user script and blocks imports outside a small, side-effect-free whitelist.

The OS-level guarantees (CPU/memory/time limits, no network) are applied by the
parent ``SubprocessCodeExecutor``; this layer is the second line of defence.
"""

import builtins
import textwrap
from typing import Any

from app.layer4_frameworks.providers.runner.return_guard import ensure_python_returns

# Builtins that are safe to expose to user scripts. Anything touching the
# filesystem, process, or import machinery is deliberately excluded.
_SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
    "callable", "chr", "complex", "dict", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "hash", "hex", "int", "isinstance",
    "issubclass", "iter", "len", "list", "map", "max", "min", "next", "oct",
    "ord", "pow", "range", "repr", "reversed", "round", "set", "slice",
    "sorted", "str", "sum", "tuple", "type", "zip", "True", "False", "None",
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "ZeroDivisionError", "ArithmeticError", "RuntimeError", "StopIteration",
)

# Standard-library modules the user script may import. All are pure/computational
# with no I/O side effects.
_ALLOWED_MODULES = frozenset({
    "json", "math", "datetime", "re", "random", "statistics",
    "itertools", "functools", "collections", "string", "decimal", "uuid",
    "base64", "hashlib", "textwrap",
})


def _build_safe_globals(logs: list[str]) -> dict[str, Any]:
    def _print(*args: Any, **kwargs: Any) -> None:
        sep = kwargs.get("sep", " ")
        logs.append(sep.join(str(a) for a in args))

    def _safe_import(name: str, *args: Any, **kwargs: Any) -> Any:
        root = name.split(".")[0]
        if root not in _ALLOWED_MODULES:
            raise ImportError(f"Import of '{name}' is not allowed in the code node")
        return builtins.__import__(name, *args, **kwargs)

    safe_builtins: dict[str, Any] = {
        n: getattr(builtins, n) for n in _SAFE_BUILTIN_NAMES if hasattr(builtins, n)
    }
    safe_builtins["print"] = _print
    safe_builtins["__import__"] = _safe_import

    return {"__builtins__": safe_builtins}


def run_python(
    code: str,
    items: list[Any],
    input_data: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    """Execute user Python ``code`` and return ``{ok, result, logs, error}``.

    The user code is wrapped in a function body, so a top-level ``return`` is
    valid and is how the node's output is produced. ``items`` and ``input`` are
    passed as arguments; for ``per_item`` mode the function is invoked once per
    element with ``item``.
    """
    logs: list[str] = []
    safe_globals = _build_safe_globals(logs)

    indented = textwrap.indent(code, "    ")
    wrapper = (
        "def __user_main(items, input, item=None):\n"
        f"{indented}\n"
    )

    try:
        compiled = compile(wrapper, "<user_code>", "exec")
        # SA-1952 — a body with no ``return`` falls off the end and yields None,
        # which used to be reported as a SUCCESS with an empty output. Refuse it
        # here, before execution: no return at all ⇒ the value is always None, so
        # this can never reject working code (a *conditional* return is untouched).
        ensure_python_returns(wrapper)
        local_ns: dict[str, Any] = {}
        exec(compiled, safe_globals, local_ns)
        user_main = local_ns["__user_main"]

        if mode == "per_item":
            result: Any = [
                user_main(items, input_data, item) for item in items
            ]
        else:
            result = user_main(items, input_data)

        # Ensure the result is JSON-serializable before handing it back.
        return {"ok": True, "result": _ensure_serializable(result), "logs": logs}
    except MemoryError:
        # Let the runner entrypoint own this one: it knows the configured limit, so
        # it can tell the user what the ceiling was and what to do about it. Swallowing
        # it here would report a bare "MemoryError:" with no ceiling and no advice.
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as script error
        return {
            "ok": False,
            "result": None,
            "logs": logs,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _ensure_serializable(value: Any) -> Any:
    """Best-effort coercion of a result into JSON-serializable form.

    ``iterencode`` walks the value and yields the JSON in small chunks, which we drop
    — so this checks serializability in O(1) memory. The obvious ``json.dumps(value)``
    would build a full copy of the serialized result just to throw it away, and for a
    code node that copy is charged against a container quota shared with the worker
    (measured: ~25% of the child's peak RSS on a large result).
    """
    import json

    try:
        for _ in json.JSONEncoder().iterencode(value):
            pass
        return value
    except (TypeError, ValueError):
        return repr(value)

"""JavaScript script execution via an embedded V8 isolate (PyMiniRacer).

Runs *inside* the isolated runner subprocess. V8 isolates have no access to the
filesystem, network, or host process by default, so the JS sandbox is provided
by the engine itself; the subprocess adds OS-level CPU/memory/time limits.
"""

import json
from typing import Any

from app.layer4_frameworks.providers.runner.return_guard import (
    CodeNodeNoReturn,
    ensure_js_returns,
)

# A small JS harness that:
#   * provides a `console` that buffers log/info/warn/error into `__logs`
#   * exposes `items` and `input` parsed from injected JSON
#   * runs the user code as a function body (so `return` produces the node output)
#   * serializes the outcome back to a JSON string for the Python side
_HARNESS = """
(function () {
  var __logs = [];
  var console = {
    log:   function () { __logs.push(Array.prototype.slice.call(arguments).map(String).join(' ')); },
    info:  function () { __logs.push(Array.prototype.slice.call(arguments).map(String).join(' ')); },
    warn:  function () { __logs.push(Array.prototype.slice.call(arguments).map(String).join(' ')); },
    error: function () { __logs.push(Array.prototype.slice.call(arguments).map(String).join(' ')); }
  };
  var items = __ITEMS__;
  var input = __INPUT__;
  var __mode = __MODE__;

  function __user_main(items, input, item) {
__USER_CODE__
  }

  try {
    var __result;
    if (__mode === 'per_item') {
      __result = items.map(function (item) { return __user_main(items, input, item); });
    } else {
      __result = __user_main(items, input, null);
    }
    if (__result === undefined) { __result = null; }
    return JSON.stringify({ ok: true, result: __result, logs: __logs });
  } catch (e) {
    var msg = (e && e.name ? e.name + ': ' : '') + (e && e.message ? e.message : String(e));
    return JSON.stringify({ ok: false, result: null, logs: __logs, error: msg });
  }
})();
"""


def run_js(
    code: str,
    items: list[Any],
    input_data: dict[str, Any],
    mode: str,
    timeout_ms: int,
) -> dict[str, Any]:
    """Execute user JavaScript ``code`` and return ``{ok, result, logs, error}``."""
    # SA-1952 — the same convention as Python, so the same guard. A body with no
    # ``return`` yields ``undefined``, which the harness used to quietly turn into
    # ``null`` and report as a success. Refuse it before the isolate even runs.
    try:
        ensure_js_returns(code)
    except CodeNodeNoReturn as exc:
        return {
            "ok": False,
            "result": None,
            "logs": [],
            "error": f"CodeNodeNoReturn: {exc}",
        }

    try:
        from py_mini_racer import MiniRacer
    except ImportError as exc:  # pragma: no cover - depends on deployment image
        return {
            "ok": False,
            "result": None,
            "logs": [],
            "error": f"JavaScript runtime unavailable: {exc}",
        }

    # User code is indented to sit inside the harness function body.
    indented = "\n".join("    " + line for line in code.splitlines())

    script = (
        _HARNESS
        .replace("__ITEMS__", json.dumps(items))
        .replace("__INPUT__", json.dumps(input_data))
        .replace("__MODE__", json.dumps(mode))
        .replace("__USER_CODE__", indented)
    )

    ctx = MiniRacer()
    try:
        # PyMiniRacer enforces its own wall-clock timeout (ms) as a fast inner
        # guard; the parent subprocess timeout is the hard outer bound.
        raw = ctx.eval(script, timeout=max(timeout_ms, 1))
    except Exception as exc:  # noqa: BLE001 - includes JSTimeoutException
        name = type(exc).__name__
        if "Timeout" in name:
            return {
                "ok": False,
                "result": None,
                "logs": [],
                "error": f"Script timed out after {timeout_ms} ms",
            }
        return {"ok": False, "result": None, "logs": [], "error": f"{name}: {exc}"}

    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        return {
            "ok": False,
            "result": None,
            "logs": [],
            "error": f"Failed to decode script result: {exc}",
        }

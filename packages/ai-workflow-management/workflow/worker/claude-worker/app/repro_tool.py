"""Browser-driving CLI for the reproduce agent.

    python -m app.repro_tool <cdp-url> <artifact-dir> '<json>'
    python -m app.repro_tool <cdp-url> <artifact-dir> -   # read JSON from stdin

The agent (``claude -p``) calls this in a loop: it reads the JSON result, decides what to
do next, and calls again. That is the whole point — the loop lives with the agent, which
is what a fixed action script could never do. Three rounds of a script-planning design
failed for exactly one reason: the planner had to guess SAPUI5 selectors before it had
ever seen the page.

What stays in code, and why:

* **The mutation guard.** Every request is inspected here and a mutating call to the
  app's own API is failed before it leaves the browser. An instruction is not a guard,
  and a regex over button labels stops being one the moment the agent writes its own
  selectors. This env is DEV but the data is a customer's, and "the agent submitted a
  real change request" is not an undoable mistake.
* **Read-only JS.** ``eval`` runs an expression and returns its value; it cannot be used
  to click, submit or navigate, because those go through the guarded actions instead.
* **The artifact trail.** Screenshots and the action log are written where the node can
  find them, so a run leaves evidence whoever drove it.

Not airtight, and worth saying plainly: the agent has Bash and Playwright is installed,
so a determined agent could drive the browser itself and miss this guard. Closing that
needs an egress proxy outside the container, which is the right hardening later. What
this gives today is a code-level default that holds unless something deliberately steps
around it.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any

# Mutating calls to the app's own API. Deliberately narrow: the app POSTs constantly for
# ordinary reads and for the upload itself, so a broad "block POST" would stop the repro
# rather than protect anything. Add to this list when a real submit path is found.
# NOTE the flag is passed as an argument, not written inline as `(?i)`. This pattern is
# also handed to Playwright's `route()`, which translates a Python regex into a JavaScript
# RegExp — JS has no inline flag syntax, so `(?i)` inside the pattern is rejected with
# "Invalid group" and the whole browser session fails to start. `re.IGNORECASE` maps to
# the JS `i` flag correctly.
_BLOCK_URL = re.compile(
    r"/(submit|approve|reject|activate|release|delete|discard|cancel"
    r"|createRequest|saveRequest|completeTask|massSubmit)\b", re.IGNORECASE)
_BLOCK_METHODS = ("POST", "PUT", "PATCH", "DELETE")

_ACTIONS = ("goto", "click", "fill", "press", "select", "upload", "wait", "wait_for",
            "text", "dom", "ui5", "eval", "probe", "shot", "url", "tabs")


def _guard(route: Any, request: Any, blocked: list[str]) -> None:
    """Fail a mutating request to a protected path; let everything else through."""
    try:
        if request.method in _BLOCK_METHODS and _BLOCK_URL.search(request.url):
            blocked.append(f"{request.method} {request.url[:160]}")
            route.abort("blockedbyclient")
            return
    except Exception:  # noqa: BLE001 — a guard that throws must not wedge the page
        pass
    route.continue_()


_UI5_JS = """
(sel) => {
  const out = [];
  const els = Array.from(document.querySelectorAll(sel)).slice(0, 25);
  for (const el of els) {
    let node = el, ctrl = null;
    // walk up to the nearest element carrying a UI5 control id
    while (node && !ctrl) {
      if (node.id && window.sap && sap.ui && sap.ui.getCore) {
        ctrl = sap.ui.getCore().byId(node.id) || null;
      }
      node = node.parentElement;
    }
    if (!ctrl) { out.push({ id: el.id || null, control: null }); continue; }
    let data = null;
    try {
      const bc = ctrl.getBindingContext && ctrl.getBindingContext();
      data = bc && bc.getObject ? bc.getObject() : null;
    } catch (e) { data = { error: String(e) }; }
    const props = {};
    for (const p of ["highlight", "text", "value", "selected", "icon", "type", "state"]) {
      try { if (ctrl["get" + p[0].toUpperCase() + p.slice(1)]) props[p] = ctrl["get" + p[0].toUpperCase() + p.slice(1)](); }
      catch (e) { /* not that kind of control */ }
    }
    out.push({ id: ctrl.getId(), type: ctrl.getMetadata ? ctrl.getMetadata().getName() : null,
               props, bindingContext: data });
  }
  return out;
}
"""


def _dom_digest(page: Any, sel: str, limit: int) -> list[dict[str, Any]]:
    """Structure of the matches: tag, classes, roles, data-* and a little text.

    This is what removes the guessing. One call and the agent knows what the table
    actually looks like, instead of trying `tr:has-text(...)` against a control that
    renders divs.
    """
    return page.evaluate(
        """([sel, limit]) => Array.from(document.querySelectorAll(sel)).slice(0, limit).map(el => ({
             tag: el.tagName.toLowerCase(),
             id: el.id || null,
             cls: (el.className || "").toString().split(/\\s+/).filter(Boolean).slice(0, 8),
             role: el.getAttribute("role"),
             data: Object.fromEntries(Array.from(el.attributes)
                     .filter(a => a.name.startsWith("data-") || a.name.startsWith("aria-"))
                     .slice(0, 8).map(a => [a.name, a.value])),
             text: (el.innerText || "").trim().slice(0, 120),
             children: el.children.length,
           }))""", [sel, limit])


def run(cdp_url: str, out_dir: str, cmds: list[dict[str, Any]]) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    os.makedirs(out_dir, exist_ok=True)
    log: list[str] = []
    blocked: list[str] = []
    results: list[dict[str, Any]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        # The guard is installed by THIS tool, on every call, so it covers whatever page
        # the agent ends up on rather than only the one it started from.
        ctx.route("**/*", lambda route, request: _guard(route, request, blocked))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()

        for i, c in enumerate(cmds, 1):
            do = str(c.get("do") or "").strip().lower()
            if do not in _ACTIONS:
                results.append({"step": i, "do": do, "error": f"unknown action; allowed: {', '.join(_ACTIONS)}"})
                continue
            t = int(c.get("timeout_ms") or 30000)
            sel = str(c.get("selector") or "")
            try:
                r: Any = "ok"
                if do == "goto":
                    page.goto(str(c["url"]), wait_until="domcontentloaded", timeout=t)
                elif do == "click":
                    (page.locator(sel) if sel else page.get_by_text(str(c.get("text")), exact=False)).first.click(timeout=t)
                elif do == "fill":
                    page.locator(sel).first.fill(str(c.get("value") or ""), timeout=t)
                elif do == "press":
                    page.keyboard.press(str(c.get("key") or "Enter"))
                elif do == "select":
                    page.locator(sel).first.select_option(str(c.get("value")))
                elif do == "upload":
                    path = str(c.get("path") or "")
                    if not os.path.exists(path):
                        raise FileNotFoundError(path)
                    target = sel or "input[type=file]"
                    if page.locator(target).count():
                        page.locator(target).first.set_input_files(path, timeout=t)
                    else:
                        # SAPUI5 keeps no <input type=file> in the DOM until its dialog
                        # opens; the visible affordance is a button.
                        with page.expect_file_chooser(timeout=t) as fc:
                            page.get_by_text(str(c.get("text") or "Upload"), exact=False).first.click(timeout=t)
                        fc.value.set_files(path)
                elif do == "wait":
                    page.wait_for_timeout(min(int(c.get("ms") or 2000), 60000))
                elif do == "wait_for":
                    if sel:
                        page.locator(sel).first.wait_for(timeout=t)
                    else:
                        page.get_by_text(str(c.get("text")), exact=False).first.wait_for(timeout=t)
                elif do == "text":
                    r = (page.locator(sel).first.inner_text(timeout=t) if sel
                         else page.locator("body").inner_text(timeout=t))[:int(c.get("limit") or 4000)]
                elif do == "dom":
                    r = _dom_digest(page, sel or "body > *", int(c.get("limit") or 25))
                elif do == "ui5":
                    r = page.evaluate(_UI5_JS, sel or ".sapMListItem")
                elif do == "eval":
                    # An expression's VALUE. Actions go through the guarded verbs above,
                    # so this stays a read.
                    r = page.evaluate(f"() => ({str(c.get('expr') or 'null')})")
                elif do == "probe":
                    loc = page.locator(sel)
                    attrs = [str(a) for a in (c.get("attrs") or [])][:8]
                    n = min(loc.count(), int(c.get("limit") or 12))
                    r = {"matches": loc.count(), "items": [
                        {"text": (loc.nth(k).inner_text(timeout=5000) or "").strip()[:120],
                         "attrs": {a: loc.nth(k).get_attribute(a) for a in attrs}}
                        for k in range(n)]}
                elif do == "shot":
                    name = re.sub(r"[^\w.-]", "_", str(c.get("name") or f"shot{i}"))[:40]
                    p = os.path.join(out_dir, f"{name}.png")
                    page.screenshot(path=p, full_page=bool(c.get("full_page")))
                    r = p
                elif do == "url":
                    r = page.url
                elif do == "tabs":
                    r = [pg.url for pg in ctx.pages]
                results.append({"step": i, "do": do, "result": r})
                log.append(f"{i} {do} ok {sel or str(c.get('text') or c.get('url') or '')[:60]}")
            except Exception as exc:  # noqa: BLE001 — a failed step is data for the agent
                msg = str(exc).splitlines()[0][:220]
                results.append({"step": i, "do": do, "error": msg})
                log.append(f"{i} {do} FAILED {msg[:80]}")
        browser.close()

    with open(os.path.join(out_dir, "actions.log"), "a", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")
    return {"results": results, "blocked_by_guard": blocked,
            "note": ("some requests were blocked by the mutation guard — that is expected, "
                     "do not try to work around it") if blocked else ""}


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(json.dumps({"error": "usage: repro_tool <cdp-url> <artifact-dir> <json|->"}))
        return 2
    raw = sys.stdin.read() if argv[3] == "-" else argv[3]
    try:
        parsed = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": f"bad JSON: {exc}"}))
        return 2
    cmds = parsed if isinstance(parsed, list) else [parsed]
    try:
        print(json.dumps(run(argv[1], argv[2], cmds), ensure_ascii=False, default=str)[:200000])
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)[:400]}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

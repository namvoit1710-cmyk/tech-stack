"""SA-1952 — `result = ...` must fail loudly, not silently yield None.

The footgun that hid a HIGH bug: a red-team test for SA-1949 was written as
``result = [60_000 rows]``, silently produced ``None``, never generated the large
payload, and made a real bug look refuted.
"""

from __future__ import annotations

import pytest

from app.layer4_frameworks.providers.runner.return_guard import (
    CodeNodeNoReturn,
    ensure_js_returns,
    ensure_python_returns,
    js_has_return,
)
from app.layer4_frameworks.providers.runner.python_runner import run_python


def _wrap(code: str) -> str:
    import textwrap
    return "def __user_main(items, input, item=None):\n" + textwrap.indent(code, "    ") + "\n"


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

class TestPythonGuard:
    def test_the_footgun_is_rejected(self):
        with pytest.raises(CodeNodeNoReturn) as exc:
            ensure_python_returns(_wrap('result = [{"sku": "A"}]'))
        msg = str(exc.value)
        assert "never returns a value" in msg
        assert "return result" in msg, "must name the exact one-line fix"

    def test_a_plain_return_is_accepted(self):
        ensure_python_returns(_wrap('return [{"sku": "A"}]'))

    def test_a_conditional_return_is_accepted(self):
        """Returning None on some branch is legitimate — no false positive."""
        ensure_python_returns(_wrap(
            "if len(items) > 0:\n"
            "    return items\n"
        ))

    def test_a_return_nested_in_a_loop_is_accepted(self):
        ensure_python_returns(_wrap(
            "for row in items:\n"
            "    if row:\n"
            "        return row\n"
        ))

    def test_a_return_only_inside_a_helper_does_not_count(self):
        """The helper returns; the node does not. This is the subtle case."""
        with pytest.raises(CodeNodeNoReturn):
            ensure_python_returns(_wrap(
                "def helper():\n"
                "    return 1\n"
                "rows = [helper()]\n"
            ))

    def test_a_lambda_return_does_not_count(self):
        with pytest.raises(CodeNodeNoReturn):
            ensure_python_returns(_wrap("f = lambda x: x\nout = f(1)\n"))

    @pytest.mark.parametrize("sink", ["result", "output", "data", "rows"])
    def test_each_sink_name_is_named_in_the_hint(self, sink):
        with pytest.raises(CodeNodeNoReturn) as exc:
            ensure_python_returns(_wrap(f"{sink} = [1]"))
        assert f"return {sink}" in str(exc.value)


class TestMessageContent:
    """The message reaches end users. It must explain OUR mechanism on its own
    terms and must not name another vendor's product."""

    @pytest.mark.parametrize("guard,src", [
        (ensure_python_returns, _wrap("result = [1]")),
        (ensure_js_returns, "const result = [1];"),
    ])
    def test_the_message_names_no_third_party_product(self, guard, src):
        with pytest.raises(CodeNodeNoReturn) as exc:
            guard(src)
        assert "n8n" not in str(exc.value).lower()

    def test_the_message_explains_the_mechanism_instead(self):
        with pytest.raises(CodeNodeNoReturn) as exc:
            ensure_python_returns(_wrap("result = [1]"))
        msg = str(exc.value)
        assert "function body" in msg, "say WHY `return` is the way to produce output"
        assert "return result" in msg, "and name the exact one-line fix"


class TestPythonRunnerEndToEnd:
    def test_run_python_fails_instead_of_returning_none(self):
        out = run_python('result = [{"sku": "A"}]', items=[], input_data={}, mode="all")
        assert out["ok"] is False, "the node must NOT report success on an empty result"
        assert "CodeNodeNoReturn" in out["error"]
        assert out["result"] is None

    def test_run_python_still_works_with_return(self):
        out = run_python('return [{"sku": "A"}]', items=[], input_data={}, mode="all")
        assert out["ok"] is True
        assert out["result"] == [{"sku": "A"}]

    def test_per_item_mode_is_guarded_too(self):
        out = run_python("x = item", items=[1, 2], input_data={}, mode="per_item")
        assert out["ok"] is False
        assert "CodeNodeNoReturn" in out["error"]

    def test_a_syntax_error_still_reports_as_a_syntax_error(self):
        """The guard must not mask a genuine syntax error — compile() runs first.

        (Note: `this is not python` is NOT a syntax error — it parses as an
        `is not` comparison — so it is a bad probe. Use an unclosed bracket.)
        """
        out = run_python("return [1, 2", items=[], input_data={}, mode="all")
        assert out["ok"] is False
        assert "SyntaxError" in out["error"]
        assert "CodeNodeNoReturn" not in out["error"]


# ---------------------------------------------------------------------------
# JavaScript — the convention is shared, so the guard must be too
# ---------------------------------------------------------------------------

class TestJsGuard:
    def test_the_footgun_is_rejected(self):
        with pytest.raises(CodeNodeNoReturn) as exc:
            ensure_js_returns("const result = items.map(r => r.sku);")
        assert "return result" in str(exc.value)

    def test_a_plain_return_is_accepted(self):
        ensure_js_returns("return items.map(r => r.sku);")

    def test_a_conditional_return_is_accepted(self):
        ensure_js_returns("if (items.length) { return items; }")

    def test_the_word_return_inside_a_string_does_not_count(self):
        with pytest.raises(CodeNodeNoReturn):
            ensure_js_returns('const msg = "please return a value";')

    def test_the_word_return_inside_a_comment_does_not_count(self):
        with pytest.raises(CodeNodeNoReturn):
            ensure_js_returns("// remember to return something\nconst out = 1;")

    def test_a_block_comment_mentioning_return_does_not_count(self):
        with pytest.raises(CodeNodeNoReturn):
            ensure_js_returns("/* return the rows */\nvar rows = [];")

    def test_returning_inside_a_nested_arrow_is_conservatively_allowed(self):
        """A false NEGATIVE (we see a return we shouldn't count) degrades to the old
        behaviour — never to rejecting valid code. Pinned so the trade-off is explicit."""
        assert js_has_return("const f = () => { return 1; }; const out = f();") is True

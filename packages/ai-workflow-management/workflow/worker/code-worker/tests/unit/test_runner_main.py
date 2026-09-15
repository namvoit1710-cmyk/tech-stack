"""Unit tests for the runner subprocess entry point (in-process, no subprocess).

Targets ``app.layer4_frameworks.providers.runner.__main__`` — the pure dispatch
+ stdin/stdout JSON contract layer. The Python/JS engines themselves are
mocked so this stays a UNIT test of the dispatch/guard/error-envelope logic,
not the sandboxed execution (which is integration-scoped in test_code_executor).
"""

import io
import json

import app.layer4_frameworks.providers.runner.__main__ as runner_main


# -- _dispatch ----------------------------------------------------------------

class TestDispatch:
    def test_python_dispatches_to_run_python(self, monkeypatch):
        # happy: language 'python' routes to run_python with unpacked job fields.
        captured = {}

        def fake_run_python(code, items, input_data, mode):
            captured.update(
                code=code, items=items, input_data=input_data, mode=mode
            )
            return {"ok": True, "result": "PY", "logs": [], "error": None}

        monkeypatch.setattr(
            "app.layer4_frameworks.providers.runner.python_runner.run_python",
            fake_run_python,
        )
        out = runner_main._dispatch({
            "language": "python",
            "code": "return 1",
            "items": [{"a": 1}],
            "input": {"a": 1},
            "mode": "per_item",
            "timeout_seconds": 7,
        })
        assert out["result"] == "PY"
        assert captured == {
            "code": "return 1",
            "items": [{"a": 1}],
            "input_data": {"a": 1},
            "mode": "per_item",
        }

    def test_javascript_dispatches_to_run_js_with_ms_timeout(self, monkeypatch):
        # happy: 'javascript' routes to run_js; timeout_seconds -> ms (*1000).
        captured = {}

        def fake_run_js(code, items, input_data, mode, timeout_ms):
            captured.update(
                code=code, items=items, input_data=input_data,
                mode=mode, timeout_ms=timeout_ms,
            )
            return {"ok": True, "result": "JS", "logs": [], "error": None}

        monkeypatch.setattr(
            "app.layer4_frameworks.providers.runner.js_runner.run_js",
            fake_run_js,
        )
        out = runner_main._dispatch({
            "language": "javascript",
            "code": "return null;",
            "items": [],
            "input": {},
            "mode": "all_items",
            "timeout_seconds": 3,
        })
        assert out["result"] == "JS"
        assert captured["timeout_ms"] == 3000

    def test_language_is_normalized_case_and_whitespace(self, monkeypatch):
        # edge: '  PYTHON  ' -> 'python' (strip().lower()).
        monkeypatch.setattr(
            "app.layer4_frameworks.providers.runner.python_runner.run_python",
            lambda *a, **k: {"ok": True, "result": "PY", "logs": [], "error": None},
        )
        out = runner_main._dispatch({"language": "  PYTHON  ", "code": "return 1"})
        assert out["result"] == "PY"

    def test_unsupported_language_returns_error_envelope(self):
        # invalid input: unknown language -> ok False + 'Unsupported language'.
        out = runner_main._dispatch({"language": "ruby", "code": "puts 1"})
        assert out["ok"] is False
        assert out["result"] is None
        assert out["logs"] == []
        assert "Unsupported language" in out["error"]
        assert "ruby" in out["error"]

    def test_empty_language_returns_unsupported(self):
        # boundary: missing/empty language falls to the unsupported branch.
        out = runner_main._dispatch({"code": "return 1"})
        assert out["ok"] is False
        assert "Unsupported language" in out["error"]

    def test_defaults_applied_for_missing_fields(self, monkeypatch):
        # boundary: absent code/items/input/mode/timeout use the ``or`` defaults.
        captured = {}

        def fake_run_python(code, items, input_data, mode):
            captured.update(
                code=code, items=items, input_data=input_data, mode=mode
            )
            return {"ok": True, "result": None, "logs": [], "error": None}

        monkeypatch.setattr(
            "app.layer4_frameworks.providers.runner.python_runner.run_python",
            fake_run_python,
        )
        runner_main._dispatch({"language": "python"})
        assert captured == {
            "code": "",
            "items": [],
            "input_data": {},
            "mode": "all_items",
        }


# -- main() (stdin -> stdout JSON contract) -----------------------------------

class TestMain:
    def _run_main(self, monkeypatch, stdin_text):
        monkeypatch.setattr(runner_main.sys, "stdin", io.StringIO(stdin_text))
        out_buf = io.StringIO()
        monkeypatch.setattr(runner_main.sys, "stdout", out_buf)
        runner_main.main()
        return out_buf.getvalue()

    def test_happy_reads_job_and_writes_result(self, monkeypatch):
        # happy: valid JSON job -> dispatched -> result JSON written to stdout.
        monkeypatch.setattr(
            "app.layer4_frameworks.providers.runner.python_runner.run_python",
            lambda *a, **k: {"ok": True, "result": 42, "logs": [], "error": None},
        )
        payload = json.dumps({"language": "python", "code": "return 42"})
        raw = self._run_main(monkeypatch, payload)
        data = json.loads(raw)
        assert data["ok"] is True
        assert data["result"] == 42

    def test_invalid_json_payload_writes_error_envelope(self, monkeypatch):
        # invalid input: malformed stdin -> 'Invalid job payload' envelope, no raise.
        raw = self._run_main(monkeypatch, "{not json")
        data = json.loads(raw)
        assert data["ok"] is False
        assert "Invalid job payload" in data["error"]

    def test_dispatch_crash_is_caught_and_reported(self, monkeypatch):
        # failure path: run_python raising -> last-resort guard 'Runner crashed'.
        def boom(*a, **k):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(
            "app.layer4_frameworks.providers.runner.python_runner.run_python",
            boom,
        )
        payload = json.dumps({"language": "python", "code": "return 1"})
        raw = self._run_main(monkeypatch, payload)
        data = json.loads(raw)
        assert data["ok"] is False
        assert "Runner crashed" in data["error"]
        assert "RuntimeError" in data["error"]
        assert "kaboom" in data["error"]

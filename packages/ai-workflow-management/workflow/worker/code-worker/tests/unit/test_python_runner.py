"""Unit tests for the Python runner (in-process, no subprocess)."""

from app.layer4_frameworks.providers.runner.python_runner import run_python


class TestRunPythonSuccess:
    def test_return_value(self):
        out = run_python("return items", [{"a": 1}], {"items": [{"a": 1}]}, "all_items")
        assert out["ok"] is True
        assert out["result"] == [{"a": 1}]

    def test_transform(self):
        code = "return [x * 2 for x in input['nums']]"
        out = run_python(code, [], {"nums": [1, 2, 3]}, "all_items")
        assert out["ok"] is True
        assert out["result"] == [2, 4, 6]

    def test_print_captured_as_logs(self):
        out = run_python("print('hello', 'world')\nreturn 1", [], {}, "all_items")
        assert out["ok"] is True
        assert out["logs"] == ["hello world"]

    def test_allowed_import(self):
        out = run_python("import math\nreturn math.floor(3.7)", [], {}, "all_items")
        assert out["ok"] is True
        assert out["result"] == 3

    def test_per_item_mode(self):
        code = "return item['n'] + 1"
        items = [{"n": 1}, {"n": 2}, {"n": 5}]
        out = run_python(code, items, {"items": items}, "per_item")
        assert out["ok"] is True
        assert out["result"] == [2, 3, 6]


class TestRunPythonSandbox:
    def test_blocked_import(self):
        out = run_python("import os\nreturn os.getcwd()", [], {}, "all_items")
        assert out["ok"] is False
        assert "not allowed" in out["error"]

    def test_open_unavailable(self):
        out = run_python("return open('/etc/passwd').read()", [], {}, "all_items")
        assert out["ok"] is False
        assert "NameError" in out["error"] or "not defined" in out["error"]

    def test_runtime_error_reported(self):
        out = run_python("return 1 / 0", [], {}, "all_items")
        assert out["ok"] is False
        assert "ZeroDivisionError" in out["error"]

    def test_syntax_error_reported(self):
        out = run_python("return (", [], {}, "all_items")
        assert out["ok"] is False
        assert out["error"]


class TestRunPythonSerialization:
    def test_non_serializable_result_coerced(self):
        out = run_python("return {1, 2, 3}", [], {}, "all_items")
        assert out["ok"] is True
        # sets are not JSON-serializable -> coerced to repr string
        assert isinstance(out["result"], str)

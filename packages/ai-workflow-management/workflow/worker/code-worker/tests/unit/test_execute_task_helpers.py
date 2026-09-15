"""Unit tests for ExecuteTaskUseCase static helpers + remaining branches.

Gap-fills the use-case lines uncovered by test_execute_task.py:
  * ``_to_int`` bool/int/valid-cast/invalid-cast branches (256, 259-262)
  * invalid ``mode`` fallback to ALL_ITEMS (173)
  * ``_normalize_items`` list / wrap / empty branches

All targets are pure (static or DI-driven) so collaborators are plain
MagicMock/FakeExecutor — no async, per the worker-sdk sync idiom.
"""

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)

from app.layer1_domain.value_objects.execution_mode import ExecutionMode
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    DEFAULT_TIMEOUT_SECONDS,
    ExecuteTaskUseCase,
)
from app.layer2_application.interfaces.code_executor_interface import CodeExecutionResult


class FakeExecutor:
    """Records the last run() call and returns a configurable result."""

    def __init__(self, result: CodeExecutionResult):
        self._result = result
        self.calls = []

    def run(self, language, code, items, input_data, mode, timeout_seconds):
        self.calls.append({
            "language": language, "code": code, "items": items,
            "input_data": input_data, "mode": mode, "timeout_seconds": timeout_seconds,
        })
        return self._result


def _uc(logger, monitor, result):
    return ExecuteTaskUseCase(
        logger=logger, monitor=monitor, code_executor=FakeExecutor(result)
    )


# -- _to_int (static) ---------------------------------------------------------

class TestToInt:
    def test_bool_returns_default(self):
        # boundary: bool is an int subclass but must NOT count as one (line 256).
        assert ExecuteTaskUseCase._to_int(True, default=30) == 30
        assert ExecuteTaskUseCase._to_int(False, default=5) == 5

    def test_int_passthrough(self):
        # happy: a real int is returned as-is.
        assert ExecuteTaskUseCase._to_int(42, default=30) == 42

    def test_numeric_string_cast(self):
        # edge: numeric string coerces via int().
        assert ExecuteTaskUseCase._to_int("15", default=30) == 15

    def test_non_numeric_string_returns_default(self):
        # invalid input: ValueError -> default (lines 259-262).
        assert ExecuteTaskUseCase._to_int("abc", default=30) == 30

    def test_none_returns_default(self):
        # invalid input: TypeError -> default.
        assert ExecuteTaskUseCase._to_int(None, default=30) == 30

    def test_float_is_truncated_by_int(self):
        # edge: float coerces through int() (truncates toward zero).
        assert ExecuteTaskUseCase._to_int(9.9, default=30) == 9


# -- _normalize_items (static) ------------------------------------------------

class TestNormalizeItems:
    def test_uses_items_array_when_present(self):
        # happy: an ``items`` list in the payload is used directly (n8n style).
        payload = {"items": [{"a": 1}, {"a": 2}], "other": "x"}
        assert ExecuteTaskUseCase._normalize_items(payload) == [{"a": 1}, {"a": 2}]

    def test_wraps_payload_when_no_items_list(self):
        # edge: no items list -> whole payload wrapped as a single item.
        payload = {"foo": "bar"}
        assert ExecuteTaskUseCase._normalize_items(payload) == [{"foo": "bar"}]

    def test_empty_payload_returns_empty_list(self):
        # boundary: empty payload -> [].
        assert ExecuteTaskUseCase._normalize_items({}) == []

    def test_items_non_list_falls_through_to_wrap(self):
        # edge: ``items`` present but not a list -> wrap whole payload.
        payload = {"items": "not-a-list"}
        assert ExecuteTaskUseCase._normalize_items(payload) == [{"items": "not-a-list"}]


# -- invalid mode fallback (branch line 173) ----------------------------------

class TestModeFallback:
    def test_invalid_mode_falls_back_to_all_items(self, logger, monitor):
        # invalid input: an unknown ``mode`` silently falls back to ALL_ITEMS,
        # NOT an error (the use-case coerces rather than raising — line 172-173).
        fake = FakeExecutor(CodeExecutionResult(success=True, result=None))
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, code_executor=fake)
        res = uc.execute(ExecuteTaskCommand(
            task_id="m1", action="code",
            inputs={"language": "python", "code": "return 1", "mode": "sideways"},
        ))
        assert res.status == TaskStatus.SUCCESS
        assert fake.calls[0]["mode"] == ExecutionMode.ALL_ITEMS.value

    def test_valid_per_item_mode_preserved(self, logger, monitor):
        # happy: a valid mode is passed through untouched.
        fake = FakeExecutor(CodeExecutionResult(success=True, result=None))
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, code_executor=fake)
        uc.execute(ExecuteTaskCommand(
            task_id="m2", action="code",
            inputs={"language": "python", "code": "return 1", "mode": "PER_ITEM"},
        ))
        assert fake.calls[0]["mode"] == ExecutionMode.PER_ITEM.value


# -- timeout boundary values (grounds the 1..120 guard in source) -------------

class TestTimeoutBoundaries:
    def test_timeout_lower_bound_ok(self, logger, monitor):
        # boundary: exactly 1 is accepted (1 <= t <= 120).
        fake = FakeExecutor(CodeExecutionResult(success=True, result=None))
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, code_executor=fake)
        res = uc.execute(ExecuteTaskCommand(
            task_id="tb1", action="code",
            inputs={"language": "python", "code": "return 1", "timeout_seconds": 1},
        ))
        assert res.status == TaskStatus.SUCCESS
        assert fake.calls[0]["timeout_seconds"] == 1

    def test_timeout_upper_bound_ok(self, logger, monitor):
        # boundary: exactly 120 (MAX) is accepted.
        fake = FakeExecutor(CodeExecutionResult(success=True, result=None))
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, code_executor=fake)
        res = uc.execute(ExecuteTaskCommand(
            task_id="tb2", action="code",
            inputs={"language": "python", "code": "return 1", "timeout_seconds": 120},
        ))
        assert res.status == TaskStatus.SUCCESS
        assert fake.calls[0]["timeout_seconds"] == 120

    def test_timeout_zero_rejected(self, logger, monitor):
        # boundary: 0 is just below the lower bound -> ValueError -> ERROR.
        uc = _uc(logger, monitor, CodeExecutionResult(success=True))
        res = uc.execute(ExecuteTaskCommand(
            task_id="tb3", action="code",
            inputs={"language": "python", "code": "return 1", "timeout_seconds": 0},
        ))
        assert res.status == TaskStatus.ERROR
        assert "timeout" in res.error.lower()

    def test_non_numeric_timeout_defaults_then_passes(self, logger, monitor):
        # invalid input: garbage timeout -> _to_int default (30) -> accepted.
        fake = FakeExecutor(CodeExecutionResult(success=True, result=None))
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, code_executor=fake)
        res = uc.execute(ExecuteTaskCommand(
            task_id="tb4", action="code",
            inputs={"language": "python", "code": "return 1", "timeout_seconds": "xyz"},
        ))
        assert res.status == TaskStatus.SUCCESS
        assert fake.calls[0]["timeout_seconds"] == DEFAULT_TIMEOUT_SECONDS

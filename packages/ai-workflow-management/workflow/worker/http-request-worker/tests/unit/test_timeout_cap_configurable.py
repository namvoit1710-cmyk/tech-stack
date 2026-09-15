"""The ``timeout_seconds`` ceiling is deployment config, not a literal.

It used to be the number 300 written in two unconnected places — a
``ValidationRule(method="max", value=300)`` in the form schema and a
``1 <= timeout_seconds <= 300`` guard in ``execute`` — so waiting longer than
five minutes on a slow-but-healthy upstream needed a code change and a rebuild.
Both now read ``Settings.HTTP_MAX_TIMEOUT_SECONDS``, injected from ``main.py``.

Two properties are worth guarding and are what these tests assert:

1. **The form and the runtime agree.** They are separate code paths fed from one
   setting; if they ever diverge the form accepts a value the worker rejects,
   which reads to the user as a random failure on a value the UI offered them.
2. **The default is unchanged at 300.** Raising the ceiling is a deployment
   decision. Nothing here changes behaviour for a deployment that sets nothing.

Note the ceiling is only half of an end-to-end raise: the executor's pending
callback deadline (TASK_TIMEOUT_MAX_SECONDS) has to cover it too, or the run
fails with "Worker http_request did not callback in time" while this worker is
still waiting. That half is guarded in the executor's own suite.
"""

from unittest.mock import patch

import pytest

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from app.layer4_frameworks.config import Settings
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
    ExecuteTaskUseCase,
    HTTP_REQUEST_INPUT_SCHEMA,
    _DEFAULT_MAX_TIMEOUT_SECONDS,
    build_http_request_input_schema,
)

from .test_execute_task_gaps import _UC, _client_mock, _mock_response


def _max_rule(schema):
    """The numeric ceiling the FE form will enforce for timeout_seconds."""
    field = next(f for f in schema if f["key"] == "timeout_seconds")
    rules = field["fieldConfig"]["rules"]
    return next(r["value"] for r in rules if r["method"] == "max")


class TestDefaultIsUnchanged:
    def test_module_default_is_still_300(self):
        assert _DEFAULT_MAX_TIMEOUT_SECONDS == 300

    def test_settings_default_is_still_300(self):
        assert Settings().HTTP_MAX_TIMEOUT_SECONDS == 300

    def test_module_schema_still_advertises_300(self):
        assert _max_rule(HTTP_REQUEST_INPUT_SCHEMA) == 300

    def test_builder_with_no_argument_returns_the_module_schema(self):
        """No config wired -> byte-for-byte the object callers already import."""
        assert build_http_request_input_schema() is HTTP_REQUEST_INPUT_SCHEMA


class TestFormCeilingFollowsConfig:
    @pytest.mark.parametrize("cap", [60, 300, 900, 3600])
    def test_builder_stamps_the_configured_ceiling(self, cap):
        assert _max_rule(build_http_request_input_schema(cap)) == cap

    def test_message_names_the_configured_ceiling(self):
        """The user reads this string in the form — a stale "300" would be a lie."""
        field = next(
            f for f in build_http_request_input_schema(900)
            if f["key"] == "timeout_seconds"
        )
        rules = field["fieldConfig"]["rules"]
        message = next(r["message"] for r in rules if r["method"] == "max")
        assert "900" in message

    def test_no_other_field_is_disturbed(self):
        raised = build_http_request_input_schema(900)
        assert [f["key"] for f in raised] == [f["key"] for f in HTTP_REQUEST_INPUT_SCHEMA]
        for built, original in zip(raised, HTTP_REQUEST_INPUT_SCHEMA):
            if built["key"] != "timeout_seconds":
                assert built == original

    def test_building_a_raised_schema_does_not_mutate_the_module_one(self):
        """The default schema is a shared module constant — a builder that edited
        it in place would silently change the ceiling for every later caller."""
        build_http_request_input_schema(3600)
        assert _max_rule(HTTP_REQUEST_INPUT_SCHEMA) == 300


class TestRuntimeGuardFollowsConfig:
    def test_default_still_rejects_above_300(self, logger, monitor):
        with patch(f"{_UC}.httpx.Client") as cls:
            uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
            result = uc.execute(ExecuteTaskCommand(
                task_id="c1", action="http_request",
                inputs={"method": "GET", "url": "https://example.com",
                        "timeout_seconds": 301},
            ))
        assert result.status == TaskStatus.ERROR
        assert "between 1 and 300" in result.error
        cls.assert_not_called()

    @patch(f"{_UC}.httpx.Client")
    def test_raised_ceiling_accepts_and_passes_the_timeout_through(self, cls, logger, monitor):
        """The whole point: 600s reaches httpx instead of being refused."""
        cls.return_value = _client_mock(_mock_response(200))
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_timeout_seconds=900)
        result = uc.execute(ExecuteTaskCommand(
            task_id="c2", action="http_request",
            inputs={"method": "GET", "url": "https://example.com",
                    "timeout_seconds": 600},
        ))
        assert result.status == TaskStatus.SUCCESS
        assert cls.return_value.stream.call_args.kwargs["timeout"] == 600

    def test_raised_ceiling_still_has_an_upper_bound(self, logger, monitor):
        with patch(f"{_UC}.httpx.Client") as cls:
            uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_timeout_seconds=900)
            result = uc.execute(ExecuteTaskCommand(
                task_id="c3", action="http_request",
                inputs={"method": "GET", "url": "https://example.com",
                        "timeout_seconds": 901},
            ))
        assert result.status == TaskStatus.ERROR
        assert "between 1 and 900" in result.error
        cls.assert_not_called()

    def test_lowered_ceiling_is_honoured(self, logger, monitor):
        """A deployment that wants a tighter bound gets one."""
        with patch(f"{_UC}.httpx.Client") as cls:
            uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_timeout_seconds=60)
            result = uc.execute(ExecuteTaskCommand(
                task_id="c4", action="http_request",
                inputs={"method": "GET", "url": "https://example.com",
                        "timeout_seconds": 120},
            ))
        assert result.status == TaskStatus.ERROR
        assert "between 1 and 60" in result.error
        cls.assert_not_called()

    def test_the_floor_is_not_configurable(self, logger, monitor):
        """Only the ceiling moved; 0 is still rejected at any configured cap."""
        with patch(f"{_UC}.httpx.Client") as cls:
            uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_timeout_seconds=900)
            result = uc.execute(ExecuteTaskCommand(
                task_id="c5", action="http_request",
                inputs={"method": "GET", "url": "https://example.com",
                        "timeout_seconds": 0},
            ))
        assert result.status == TaskStatus.ERROR
        assert "between 1 and 900" in result.error
        cls.assert_not_called()


class TestFormAndRuntimeAgree:
    """The property that makes the pair safe, asserted directly."""

    @pytest.mark.parametrize("cap", [60, 300, 900, 3600])
    def test_the_value_the_form_allows_is_the_value_the_worker_allows(
        self, cap, logger, monitor
    ):
        assert _max_rule(build_http_request_input_schema(cap)) == cap

        with patch(f"{_UC}.httpx.Client") as cls:
            cls.return_value = _client_mock(_mock_response(200))
            uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_timeout_seconds=cap)
            at_cap = uc.execute(ExecuteTaskCommand(
                task_id="a1", action="http_request",
                inputs={"method": "GET", "url": "https://example.com",
                        "timeout_seconds": cap},
            ))
        assert at_cap.status == TaskStatus.SUCCESS, "form allows the cap; worker must too"

        with patch(f"{_UC}.httpx.Client") as cls:
            uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, max_timeout_seconds=cap)
            over_cap = uc.execute(ExecuteTaskCommand(
                task_id="a2", action="http_request",
                inputs={"method": "GET", "url": "https://example.com",
                        "timeout_seconds": cap + 1},
            ))
        assert over_cap.status == TaskStatus.ERROR
        cls.assert_not_called()

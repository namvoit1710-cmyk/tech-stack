"""Smoke tests that the worker wiring imports and exposes valid schemas."""

from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    CODE_INPUT_SCHEMA,
    CODE_OUTPUT_SCHEMA,
    ExecuteTaskUseCase,
)
from app.layer4_frameworks.providers.subprocess_code_executor import SubprocessCodeExecutor


class TestBootstrap:
    def test_schemas_are_lists(self):
        assert isinstance(CODE_INPUT_SCHEMA, list) and CODE_INPUT_SCHEMA
        assert isinstance(CODE_OUTPUT_SCHEMA, list) and CODE_OUTPUT_SCHEMA

    def test_schema_entries_are_dicts_with_keys(self):
        for field in CODE_INPUT_SCHEMA + CODE_OUTPUT_SCHEMA:
            assert isinstance(field, dict)
            assert "key" in field

    def test_usecase_accepts_executor_injection(self):
        uc = ExecuteTaskUseCase(
            logger=None, monitor=None, code_executor=SubprocessCodeExecutor()
        )
        assert uc.code_executor is not None

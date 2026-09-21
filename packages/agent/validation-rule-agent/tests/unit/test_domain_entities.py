"""Behaviour-parity tests for the Layer-1 domain entities (SA-1444)."""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.agent_response import AgentResponse
from app.layer1_domain.entities.execution_plan import ExecutionPlan
from app.layer1_domain.entities.execution_result import ExecutionResult
from app.layer1_domain.entities.template_field import TemplateField
from app.layer1_domain.entities.validation_request import ValidationRequest


class TestValidationRequest:
    def test_message_only_ok(self):
        req = ValidationRequest.from_state({"message": "check email", "parameters": {}})
        assert req.message == "check email"
        assert req.is_generate_polar() is False

    def test_rules_only_ok(self):
        req = ValidationRequest.from_state({"parameters": {"rules": [{"scope": "column"}]}})
        assert req.params["rules"] == [{"scope": "column"}]

    def test_missing_both_raises(self):
        with pytest.raises(ValueError, match="Missing required parameters"):
            ValidationRequest.from_state({"parameters": {}})

    def test_is_generate_polar(self):
        req = ValidationRequest.from_state({"message": "x", "parameters": {"polar_type": "generate_polar"}})
        assert req.is_generate_polar() is True


class TestExecutionPlan:
    def test_from_rules_list(self):
        assert ExecutionPlan.from_rules([{"scope": "column"}]).steps == [{"scope": "column"}]

    def test_from_rules_dict(self):
        plan = ExecutionPlan.from_rules({"execution_plan": [{"scope": "column"}], "status": "ok"})
        assert plan.steps == [{"scope": "column"}]
        assert plan.status == "ok"

    def test_from_rules_dict_without_plan_raises(self):
        with pytest.raises(ValueError, match="must include 'execution_plan'"):
            ExecutionPlan.from_rules({"foo": 1})

    def test_from_rules_bad_type_raises(self):
        with pytest.raises(ValueError, match="must be a list or an object"):
            ExecutionPlan.from_rules("nope")

    def test_from_planner_not_supported(self):
        plan = ExecutionPlan.from_planner({"status": "not_supported", "message": "no"})
        assert plan.is_not_supported() is True
        assert plan.message == "no"

    def test_grouped_by_scope(self):
        steps = [{"scope": "column", "a": 1}, {"scope": "column", "a": 2}, {"scope": "row"}]
        grouped = list(ExecutionPlan(steps=steps).grouped_by_scope())
        assert [s for s, _ in grouped] == ["column", "row"]
        assert grouped[0][1] == steps[:2]

    def test_single_scope(self):
        assert ExecutionPlan(steps=[{"scope": "column"}, {"scope": "column"}]).single_scope() == "column"
        assert ExecutionPlan(steps=[{"scope": "column"}, {"scope": "row"}]).single_scope() is None

    def test_single_scope_missing_raises(self):
        with pytest.raises(ValueError, match="must include 'scope'"):
            ExecutionPlan(steps=[{"foo": "bar"}]).single_scope()


class TestExecutionResult:
    def test_rule_only(self):
        r = ExecutionResult.rule_only([{"scope": "column"}])
        assert r.message == "Rules generated successfully."
        assert r.rules == [{"scope": "column"}]
        assert r.has_result is False
        assert r.validate_status is True
        assert r.file_url == ""

    def test_from_executor(self):
        r = ExecutionResult.from_executor(
            {"result": {"invalid_rows": 0, "odata": {"result_file_id": "f1", "result_file_url": "http://x/f1"}}}
        )
        assert r.has_result is True
        assert r.validate_status is True
        assert r.file_id == "f1"
        assert r.file_url == "http://x/f1"

    def test_from_executor_invalid_rows_marks_false(self):
        r = ExecutionResult.from_executor({"result": {"invalid_rows": 3, "odata": {}}})
        assert r.validate_status is False


class TestAgentResponse:
    def test_polar_success_and_error(self):
        ok = AgentResponse.polar({"valid": "correct"}, "validation-rule").to_dict()["formatted_response"]
        assert ok["status"] == "success"
        assert ok["content"] == "Transform generated successfully."

        bad = AgentResponse.polar({"valid": "incorrect", "error": "nope"}, "validation-rule").to_dict()["formatted_response"]
        assert bad["status"] == "error"
        assert bad["content"] == "nope"

    def test_error(self):
        fr = AgentResponse.error(RuntimeError("boom"), "validation-rule").to_dict()["formatted_response"]
        assert fr["status"] == "error"
        assert fr["content"] == "Validation Rule Agent execute fail. boom"
        assert fr["agent_data"]["valid"] == "incorrect"

    def test_not_supported_default(self):
        fr = AgentResponse.not_supported("validation-rule", None).to_dict()["formatted_response"]
        assert fr["content"] == "Validation Rule Agent not supported."
        assert fr["status"] == "error"

    def test_success_with_url_and_validate_status(self):
        result = ExecutionResult.from_executor(
            {
                "rules": [{"scope": "column"}],
                "result": {"invalid_rows": 0, "odata": {"result_file_id": "f1", "result_file_url": "http://x/f1"}},
            }
        )
        fr = AgentResponse.success("done", result, "validation-rule").to_dict()["formatted_response"]
        assert fr["content"] == "done\n\nDownload detailed report: http://x/f1"
        assert fr["agent_data"]["validate_status"] is True
        assert fr["agent_data"]["file_id"] == "f1"

    def test_success_invalid_rows_marks_false(self):
        result = ExecutionResult.from_executor({"rules": [], "result": {"invalid_rows": 3, "odata": {}}})
        fr = AgentResponse.success("done", result, "validation-rule").to_dict()["formatted_response"]
        assert fr["content"] == "done"
        assert fr["agent_data"]["validate_status"] is False


class TestTemplateField:
    def test_build_header(self):
        aliases, header = TemplateField.build_header(
            [TemplateField("t", "f", "A", "s"), TemplateField("t", "f2", "B", "s")]
        )
        assert aliases == ["A", "B"]
        assert header == {"A": "", "B": ""}

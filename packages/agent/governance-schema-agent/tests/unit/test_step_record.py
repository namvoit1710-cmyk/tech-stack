"""Unit specs for layer1 domain entity `StepRecord`.

Target: app/layer1_domain/entities/step_record.py
Unit under test: the pure StepRecord dataclass + its static builders/validators.
No collaborators -> no mocks (layer1 domain rules are pure). Every expected
value below is grounded in the source, not in convention.

Case coverage per unit: happy / edge / invalid input / boundary / failure path.
"""
from __future__ import annotations

import json

import pytest

from app.layer1_domain.entities.enums import (
    ApprovalProcessAction,
    ApprovalResolutionStrategy,
    AssignType,
    ContributeResolutionStrategy,
    ProcessExecutionMode,
    Validity,
)
from app.layer1_domain.entities.section_definition import SectionDefinition
from app.layer1_domain.entities.step_record import StepRecord


# ---------------------------------------------------------------------------
# normalize_message_label
# ---------------------------------------------------------------------------
class TestNormalizeMessageLabel:
    def test_happy_passthrough_known_message(self):
        # happy: a plain message with no alias is returned stripped, unchanged
        assert (
            StepRecord.normalize_message_label({"message": "Found suitable section"})
            == "Found suitable section"
        )

    def test_edge_alias_is_mapped(self):
        # edge: MESSAGE_ALIASES maps "Find suitable section" -> "Found suitable section"
        assert (
            StepRecord.normalize_message_label({"message": "Find suitable section"})
            == "Found suitable section"
        )
        assert (
            StepRecord.normalize_message_label({"message": "No section mentioned"})
            == "Wrong section"
        )

    def test_boundary_step_default_overrides_message(self):
        # boundary: step_default True short-circuits to "Default step" regardless of message
        assert (
            StepRecord.normalize_message_label(
                {"step_default": True, "message": "Not related"}
            )
            == "Default step"
        )

    def test_invalid_missing_message_returns_empty_string(self):
        # invalid input: no message key -> str("") -> "" (grounded: str(data.get("message","")))
        assert StepRecord.normalize_message_label({}) == ""

    def test_edge_whitespace_is_stripped(self):
        assert (
            StepRecord.normalize_message_label({"message": "  Wrong section  "})
            == "Wrong section"
        )


# ---------------------------------------------------------------------------
# normalize_parts
# ---------------------------------------------------------------------------
class TestNormalizeParts:
    def test_happy_string_passthrough(self):
        assert StepRecord.normalize_parts('[{"section":"A"}]') == '[{"section":"A"}]'

    def test_boundary_none_becomes_empty_array_string(self):
        assert StepRecord.normalize_parts(None) == "[]"

    def test_edge_list_is_json_dumped(self):
        assert StepRecord.normalize_parts([{"section": "A"}]) == json.dumps(
            [{"section": "A"}]
        )

    def test_boundary_empty_list_dumped(self):
        assert StepRecord.normalize_parts([]) == "[]"


# ---------------------------------------------------------------------------
# validate_required_fields
# ---------------------------------------------------------------------------
class TestValidateRequiredFields:
    def _full(self) -> dict:
        # A record with every non-optional field present and truthy.
        return {
            "description": "edit General Material Data",
            "parts": "[]",
            "assignToId": "a@company.com",
            "allowActions": ["EDIT"],
        }

    def test_happy_all_required_present(self):
        validity, field = StepRecord.validate_required_fields(self._full())
        assert validity is Validity.CORRECT
        assert field is None

    def test_failure_missing_required_field_reported(self):
        data = self._full()
        del data["description"]
        validity, field = StepRecord.validate_required_fields(data)
        assert validity is Validity.INCORRECT
        assert field == "description"

    def test_edge_step_default_skips_validation(self):
        # edge: step_default True returns CORRECT immediately (grounded: early return)
        validity, field = StepRecord.validate_required_fields({"step_default": True})
        assert validity is Validity.CORRECT
        assert field is None

    def test_invalid_blank_string_required_field(self):
        # invalid input: whitespace-only description is treated as missing
        data = self._full()
        data["description"] = "   "
        validity, field = StepRecord.validate_required_fields(data)
        assert validity is Validity.INCORRECT
        assert field == "description"

    def test_boundary_assign_to_id_blank(self):
        data = self._full()
        data["assignToId"] = ""
        validity, field = StepRecord.validate_required_fields(data)
        assert validity is Validity.INCORRECT
        assert field == "assignToId"


# ---------------------------------------------------------------------------
# validate_data_section
# ---------------------------------------------------------------------------
class TestValidateDataSection:
    def test_happy_non_empty_is_correct(self):
        assert StepRecord.validate_data_section({"section": "A"}) is Validity.CORRECT

    def test_boundary_empty_dict_is_incorrect(self):
        assert StepRecord.validate_data_section({}) is Validity.INCORRECT

    def test_invalid_none_is_incorrect(self):
        assert StepRecord.validate_data_section(None) is Validity.INCORRECT


# ---------------------------------------------------------------------------
# from_dict  (the rich branching builder)
# ---------------------------------------------------------------------------
class TestFromDict:
    def _sections(self) -> list[SectionDefinition]:
        return [
            SectionDefinition(section="Supply Chain", fields=["MRP type", "lot size"]),
            SectionDefinition(section="Finance", fields=["valuation class"]),
        ]

    def test_happy_found_suitable_section(self):
        # happy: message "Found suitable section" with a matching part -> CORRECT
        data = {
            "description": "Line 1: edit Supply Chain",
            "stepId": 1,
            "stepOrder": 1,
            "message": "Found suitable section",
            "parts": '[{"section":"Supply Chain"}]',
            "assignType": "USER",
            "assignToId": "a@company.com",
            "allowActions": ["EDIT"],
            "contributeResolutionStrategy": "ALL_CONTRIBUTED",
            "approvalResolutionStrategy": None,
            "executionMode": "SINGLE_TASK",
            "nextSteps": "2",
            "skip": False,
        }
        step = StepRecord.from_dict(data, self._sections())
        assert step.valid is Validity.CORRECT
        assert step.message == "Found suitable section"
        # description has the "Line N:" prefix stripped (grounded: re.sub in from_dict)
        assert step.description == "edit Supply Chain"
        assert step.assignType is AssignType.USER
        assert step.contributeResolutionStrategy is ContributeResolutionStrategy.ALL_CONTRIBUTED
        assert step.executionMode is ProcessExecutionMode.SINGLE_TASK
        # allowActions is json.dumps of enum members -> their str() form
        assert step.allowActions == json.dumps([ApprovalProcessAction("EDIT")])

    def test_edge_found_section_downgraded_to_wrong_when_part_not_allowed(self):
        # edge: message claims Found suitable section but the part is not in allowed set
        data = {
            "description": "edit Unknown Area",
            "stepId": 1,
            "stepOrder": 1,
            "message": "Found suitable section",
            "parts": '[{"section":"Unknown Area"}]',
            "assignToId": "a@company.com",
            "allowActions": ["EDIT"],
            "nextSteps": "",
            "skip": False,
        }
        step = StepRecord.from_dict(data, self._sections())
        assert step.message == "Wrong section"
        assert step.valid is Validity.INCORRECT
        assert step.skip is False

    def test_edge_default_step_case_skip_true(self):
        # message "Default step" -> CASE_SKIP True and CASE_VALIDITY CORRECT
        data = {
            "description": "Route to Steward to troubleshoot",
            "stepId": 5,
            "stepOrder": 2,
            "message": "Default step",
            "parts": "[]",
            "assignToId": "STEWARD",
            "allowActions": ["TROUBLESHOOT"],
            "nextSteps": "",
            "step_default": True,
        }
        step = StepRecord.from_dict(data, self._sections())
        assert step.valid is Validity.CORRECT
        assert step.skip is True
        assert step.step_default is True

    def test_boundary_unknown_message_with_matching_part_promoted_to_found(self):
        # boundary: message not in CASE_VALIDITY -> else branch; a matching part
        # promotes it to Found suitable section (grounded: the final else in from_dict)
        data = {
            "description": "do something with Finance",
            "stepId": 1,
            "stepOrder": 1,
            "message": "Some Novel Label",
            "parts": '[{"section":"Finance"}]',
            "assignToId": "x@co.com",
            "allowActions": None,
            "nextSteps": "",
        }
        step = StepRecord.from_dict(data, self._sections())
        assert step.message == "Found suitable section"
        assert step.valid is Validity.CORRECT
        # allowActions None -> stays None (grounded: ... if data.get("allowActions") is not None else None)
        assert step.allowActions is None

    def test_boundary_unknown_message_no_part_becomes_wrong_section(self):
        data = {
            "description": "vague step",
            "stepId": 1,
            "stepOrder": 1,
            "message": "Some Novel Label",
            "parts": "[]",
            "assignToId": "x@co.com",
            "allowActions": None,
            "nextSteps": "",
        }
        step = StepRecord.from_dict(data, self._sections())
        assert step.message == "Wrong section"
        assert step.valid is Validity.INCORRECT

    def test_invalid_parts_not_a_list_raises_value_error(self):
        # invalid input: parts json-parses to a dict, not a list -> ValueError,
        # re-wrapped as "Enum validation error:" by the except ValueError block.
        data = {
            "description": "x",
            "stepId": 1,
            "stepOrder": 1,
            "message": "No need section",
            "parts": '{"section":"Finance"}',
            "assignToId": "x@co.com",
            "allowActions": None,
            "nextSteps": "",
        }
        with pytest.raises(ValueError):
            StepRecord.from_dict(data, self._sections())

    def test_failure_non_int_step_id_raises(self):
        # failure path: stepId cannot be int() -> ValueError bubbles through
        data = {
            "description": "x",
            "stepId": "not-a-number",
            "stepOrder": 1,
            "message": "No need section",
            "parts": "[]",
            "assignToId": "x@co.com",
            "allowActions": None,
            "nextSteps": "",
        }
        with pytest.raises(ValueError):
            StepRecord.from_dict(data, self._sections())

    def test_boundary_none_next_steps_becomes_empty_string(self):
        # boundary: nextSteps None -> "" (grounded: data.get("nextSteps") or "")
        data = {
            "description": "x",
            "stepId": 1,
            "stepOrder": 1,
            "message": "No need section",
            "parts": "[]",
            "assignToId": "x@co.com",
            "allowActions": None,
            "nextSteps": None,
        }
        step = StepRecord.from_dict(data, self._sections())
        assert step.nextSteps == ""


# ---------------------------------------------------------------------------
# to_dict  (round-trip / enum unwrapping)
# ---------------------------------------------------------------------------
class TestToDict:
    def test_happy_enums_unwrapped_to_values(self):
        step = StepRecord(
            description="edit Supply Chain",
            stepId=1,
            stepOrder=1,
            parts="[]",
            assignType=AssignType.USER,
            assignToId="a@co.com",
            allowActions=None,
            contributeResolutionStrategy=ContributeResolutionStrategy.ALL_CONTRIBUTED,
            approvalResolutionStrategy=ApprovalResolutionStrategy.ALL_APPROVED,
            executionMode=ProcessExecutionMode.SINGLE_TASK,
            nextSteps="",
            skip=False,
            message="Found suitable section",
            valid=Validity.CORRECT,
            step_default=False,
        )
        d = step.to_dict()
        assert d["valid"] == "correct"
        assert d["assignType"] == "USER"
        assert d["contributeResolutionStrategy"] == "ALL_CONTRIBUTED"
        assert d["approvalResolutionStrategy"] == "ALL_APPROVED"
        assert d["executionMode"] == "SINGLE_TASK"

    def test_boundary_none_enums_stay_none(self):
        step = StepRecord(
            description="x",
            stepId=1,
            stepOrder=1,
            parts=None,
            assignType=None,
            assignToId="",
            allowActions=None,
            contributeResolutionStrategy=None,
            approvalResolutionStrategy=None,
            executionMode=None,
            nextSteps="",
            skip=False,
        )
        d = step.to_dict()
        assert d["assignType"] is None
        assert d["executionMode"] is None
        # normalize_parts(None) -> "[]"
        assert d["parts"] == "[]"

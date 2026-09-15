"""Tests for centralized ErrorCode enum."""

from app.layer2_application.errors import ErrorCode


class TestErrorCodeEnum:
    def test_all_codes_unique(self):
        codes = [member.code for member in ErrorCode]
        assert len(codes) == len(set(codes)), f"Duplicate codes: {[c for c in codes if codes.count(c) > 1]}"

    def test_all_http_statuses_valid(self):
        for member in ErrorCode:
            assert 400 <= member.http_status <= 599, (
                f"{member.name} has invalid http_status {member.http_status}"
            )

    def test_all_default_messages_nonempty(self):
        for member in ErrorCode:
            assert member.default_message, f"{member.name} has empty default_message"

    def test_from_code_found(self):
        ec = ErrorCode.from_code("RUN_NOT_FOUND")
        assert ec is not None
        assert ec == ErrorCode.RUN_NOT_FOUND
        assert ec.http_status == 404

    def test_from_code_not_found(self):
        assert ErrorCode.from_code("DOES_NOT_EXIST") is None

    def test_code_matches_name(self):
        for member in ErrorCode:
            assert member.code == member.name, (
                f"{member.name}.code is '{member.code}' but should match name"
            )

    def test_not_found_codes_are_404(self):
        not_found_codes = [
            ErrorCode.RUN_NOT_FOUND,
            ErrorCode.WORKFLOW_NOT_FOUND,
            ErrorCode.NODE_NOT_FOUND,
            ErrorCode.TASK_NOT_FOUND,
            ErrorCode.WEBHOOK_NOT_FOUND,
            ErrorCode.NODE_DEFINITION_NOT_FOUND,
            ErrorCode.PARENT_DEFINITION_NOT_FOUND,
            ErrorCode.ARTIFACT_NOT_FOUND,
            ErrorCode.NOT_FOUND,
        ]
        for ec in not_found_codes:
            assert ec.http_status == 404, f"{ec.name} should be 404"

    def test_validation_codes_are_400(self):
        validation_codes = [
            ErrorCode.VALIDATION_ERROR,
            ErrorCode.INVALID_NODE_DATA,
            ErrorCode.INVALID_EDGE_DATA,
            ErrorCode.INVALID_EDGE_REFERENCE,
            ErrorCode.INVALID_WORKFLOW,
            ErrorCode.INPUT_VALIDATION_FAILED,
            ErrorCode.NOTHING_TO_EDIT,
        ]
        for ec in validation_codes:
            assert ec.http_status == 400, f"{ec.name} should be 400"

    def test_state_conflict_codes_are_409(self):
        conflict_codes = [
            ErrorCode.INVALID_RUN_STATUS,
            ErrorCode.INVALID_TASK_STATUS,
            ErrorCode.CANNOT_DELETE_IN_USE,
        ]
        for ec in conflict_codes:
            assert ec.http_status == 409, f"{ec.name} should be 409"

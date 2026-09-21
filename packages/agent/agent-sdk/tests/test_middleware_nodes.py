class PassingInputGuard:
    def validate(self, message: str, **context) -> dict:
        return {"passed": True, "sanitized_message": message, "warnings": []}


class RejectingInputGuard:
    def validate(self, message: str, **context) -> dict:
        return {"passed": False, "rejection_reason": "bad input", "warnings": []}


class PassingOutputGuard:
    def validate(self, response: dict, **context) -> dict:
        return {"passed": True, "sanitized_response": response, "warnings": []}


class RejectingOutputGuard:
    def validate(self, response: dict, **context) -> dict:
        return {"passed": False, "rejection_reason": "bad output", "warnings": []}


class TestInputGuardNode:
    def test_passes_through_when_no_guard(self):
        from agent_sdk.layer2_application.services.middleware.input_guard_node import (
            input_guard_node,
        )

        state = {"message": "hello"}
        deps = {}
        result = input_guard_node(state, deps)
        assert result.get("error") is None

    def test_sets_error_when_guard_rejects(self):
        from agent_sdk.layer2_application.services.middleware.input_guard_node import (
            input_guard_node,
        )

        state = {"message": "bad message", "user_id": "u1"}
        deps = {"input_guard": RejectingInputGuard()}
        result = input_guard_node(state, deps)
        assert result.get("error") == "bad input"
        assert result.get("error_code") is not None
        assert result["input_guard_result"]["passed"] is False


class TestOutputGuardNode:
    def test_passes_through_when_no_guard(self):
        from agent_sdk.layer2_application.services.middleware.output_guard_node import (
            output_guard_node,
        )

        state = {"formatted_response": {"content": "hello"}}
        deps = {}
        result = output_guard_node(state, deps)
        assert result.get("error") is None

    def test_sets_error_when_guard_rejects(self):
        from agent_sdk.layer2_application.services.middleware.output_guard_node import (
            output_guard_node,
        )

        state = {"formatted_response": {"content": "bad response"}}
        deps = {"output_guard": RejectingOutputGuard()}
        result = output_guard_node(state, deps)
        assert result.get("error") == "bad output"
        assert result.get("error_code") is not None
        assert result["output_guard_result"]["passed"] is False


class TestErrorHandlerNode:
    def test_populates_formatted_response_from_error(self):
        from agent_sdk.layer2_application.services.middleware.error_handler_node import (
            error_handler_node,
        )

        state = {"error": "something went wrong", "error_code": "SDK_001"}
        deps = {}
        result = error_handler_node(state, deps)
        assert result.get("formatted_response") is not None
        formatted = result["formatted_response"]
        assert "something went wrong" in str(formatted)
        assert result.get("transport_state") == "ERROR"

    def test_formatted_response_has_explicit_error_status(self):
        from agent_sdk.layer2_application.services.middleware.error_handler_node import (
            error_handler_node,
        )

        state = {"error": "something went wrong", "error_code": "SDK_001"}
        deps = {}
        result = error_handler_node(state, deps)
        formatted = result["formatted_response"]
        assert formatted.get("status") == "error", (
            "formatted_response must include status='error' so that "
            "formatted.get('status', 'success') returns 'error', not 'success'"
        )


class TestFormatResponseNode:
    def test_wraps_result_in_formatted_response(self):
        from agent_sdk.layer2_application.services.middleware.format_response_node import (
            format_response_node,
        )

        state = {"agent_result": {"content": "final answer"}}
        deps = {}
        result = format_response_node(state, deps)
        assert result.get("formatted_response") is not None
        assert result.get("transport_state") == "COMPLETED"
        formatted = result["formatted_response"]
        assert "final answer" in str(formatted)

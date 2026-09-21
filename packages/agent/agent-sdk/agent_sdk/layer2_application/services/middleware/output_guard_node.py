def output_guard_node(state: dict, deps: dict) -> dict:
    guard = deps.get("output_guard")
    if guard is None:
        return {}
    response = state.get("formatted_response", {})
    result = guard.validate(response)
    if not result.get("passed", False):
        rejection_reason = result.get("rejection_reason", "Output rejected")
        return {
            "output_guard_result": {
                "passed": False,
                "rejection_reason": rejection_reason,
            },
            "error": rejection_reason,
            "error_code": "SDK_OUTPUT_001",
        }
    update: dict = {
        "output_guard_result": {"passed": True, "warnings": result.get("warnings", [])}
    }
    sanitized = result.get("sanitized_response")
    if sanitized is not None:
        update["formatted_response"] = sanitized
    return update

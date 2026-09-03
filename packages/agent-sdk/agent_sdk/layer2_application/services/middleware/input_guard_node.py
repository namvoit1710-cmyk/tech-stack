def input_guard_node(state: dict, deps: dict) -> dict:
    guard = deps.get("input_guard")
    if guard is None:
        return {}
    message = state.get("message", "")
    user_id = state.get("user_id", "anonymous")
    result = guard.validate(message, user_id=user_id)
    if not result.get("passed", False):
        rejection_reason = result.get("rejection_reason", "Input rejected")
        return {
            "input_guard_result": {
                "passed": False,
                "rejection_reason": rejection_reason,
            },
            "error": rejection_reason,
            "error_code": "SDK_INPUT_001",
        }
    update: dict = {
        "input_guard_result": {"passed": True, "warnings": result.get("warnings", [])}
    }
    sanitized = result.get("sanitized_message")
    if sanitized is not None:
        update["message"] = sanitized
    return update

from __future__ import annotations

from typing import Any, Mapping


def _coerce_error_context(state: Mapping[str, Any]) -> dict[str, Any] | None:
    error_context = state.get("error_context")
    if isinstance(error_context, dict):
        return dict(error_context)

    error = state.get("error_object")
    if error is None:
        return None

    serializer = getattr(error, "to_error_context", None)
    if callable(serializer):
        return serializer()

    return None


def error_handler_node(state: dict, deps: dict) -> dict:
    error_context = _coerce_error_context(state)
    error_msg = state.get("error", "An unexpected error occurred")
    error_code = state.get("error_code") or "SDK_ERR_001"
    related_step_id = None
    is_critical = False

    if error_context is not None:
        error_msg = error_context.get("message") or error_msg
        error_code = error_context.get("error_code") or error_code
        related_step_id = error_context.get("related_step_id")
        is_critical = bool(
            error_context.get("is_critical", error_context.get("critical"))
        )

    conv_id = state.get("conv_id", "")
    logger = deps.get("logger")
    if logger is not None:
        logger.error(
            "Error handled",
            error=error_msg,
            error_code=error_code,
            conv_id=conv_id,
            related_step_id=related_step_id,
            is_critical=is_critical,
        )

    formatted_response = {
        "type": "error",
        "status": "error",
        "content": error_msg,
        "error": error_msg,
        "error_code": error_code,
        "conv_id": conv_id,
    }
    if related_step_id:
        formatted_response["related_step_id"] = related_step_id
    if error_context is not None:
        formatted_response["is_critical"] = is_critical
        formatted_response["error_context"] = error_context
    return {"formatted_response": formatted_response, "transport_state": "ERROR"}

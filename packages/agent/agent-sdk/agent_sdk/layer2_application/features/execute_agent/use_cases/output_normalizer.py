from __future__ import annotations

from typing import Any


def normalize_final_state(final_state: dict[str, Any]) -> dict[str, Any]:
    """Return normalized kwargs consumable by execute/resume output mappers.

    Priority order for deriving final output:
    1. formatted_response
    2. top-level error
    3. agent_result
    4. top-level agent_data + message + status
    """
    formatted = final_state.get("formatted_response")
    if formatted:
        return {
            "message": formatted.get("content", ""),
            "status": formatted.get("status", "success"),
            "agent_data": formatted.get("agent_data", {}),
            "data": formatted.get("data", {}),
            "error": formatted.get("error"),
            "error_code": formatted.get("error_code"),
            "related_step_id": formatted.get("related_step_id"),
            "is_critical": bool(
                formatted.get("is_critical", formatted.get("critical", False))
            ),
            "error_context": formatted.get("error_context"),
        }

    error = final_state.get("error")
    if error:
        return {
            "message": "",
            "status": "error",
            "agent_data": {},
            "data": {},
            "error": error,
            "error_code": final_state.get("error_code", ""),
            "related_step_id": final_state.get("related_step_id"),
            "is_critical": bool(
                final_state.get("is_critical", final_state.get("critical", False))
            ),
            "error_context": final_state.get("error_context"),
        }

    agent_result = final_state.get("agent_result")
    if agent_result is not None:
        if isinstance(agent_result, dict):
            message = agent_result.get("content") or agent_result.get("message") or ""
            payload_status = agent_result.get("status")
            if payload_status:
                status = payload_status
            elif agent_result.get("success") is True:
                status = "success"
            elif agent_result.get("success") is False:
                status = "error"
            else:
                status = "success"
        else:
            message = str(agent_result)
            status = "success"
        return {
            "message": message,
            "status": status,
            "agent_data": agent_result if isinstance(agent_result, dict) else {},
            "data": {},
            "error": None,
            "error_code": None,
            "related_step_id": None,
            "is_critical": False,
            "error_context": None,
        }

    agent_data = final_state.get("agent_data")
    if agent_data is not None:
        message = final_state.get("message", "")
        top_status = final_state.get("status")
        if top_status:
            status = top_status
        elif final_state.get("success") is True:
            status = "success"
        elif final_state.get("success") is False:
            status = "error"
        else:
            status = "success"
        return {
            "message": message,
            "status": status,
            "agent_data": agent_data,
            "data": {},
            "error": None,
            "error_code": None,
            "related_step_id": None,
            "is_critical": False,
            "error_context": None,
        }

    return {
        "message": final_state.get("message", ""),
        "status": "success",
        "agent_data": {},
        "data": {},
        "error": None,
        "error_code": None,
        "related_step_id": None,
        "is_critical": False,
        "error_context": None,
    }

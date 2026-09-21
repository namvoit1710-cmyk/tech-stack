from __future__ import annotations

import json

from agent_sdk import OpenAIService, tool
from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter


async def _revert_file_impl(revert_instruction: str, conversation_history: str) -> dict[str, str]:
    requested_target = revert_instruction.strip()
    history_prompt = conversation_history.strip()
    if not history_prompt:
        error_message = "Missing conversation history"
        return {"error": error_message, "message": error_message}

    if not requested_target:
        requested_target = "Select the correct older file version requested by the user from the conversation history."

    system_prompt = f"""
You are a file history revert assistant.

Your task is to identify the exact old file version requested by the user from a conversation history.

The conversation_history input is a rendered plain-text log, not JSON.
Read it using this structure:
- The first line may be "Conversation history:".
- Each following turn line uses this format:
    - turn <number> | role=<role> | file_id=<file_id> | message=<message>
- Some turn lines may omit file_id and only include role and message.
- turn numbers increase chronologically from oldest to newest.

Rules:
- Follow the requested revert target first.
- The requested revert target may describe time, turn order, relative age, or another older version reference.
- The conversation is chronological.
- The last user turn in the history may be the current request, but prefer the explicit requested revert target when it is provided.
- For revert, turn back, previous version, or undo requests, choose the matching older distinct file_id from the history.
- If the request says to revert the current file_id to a previous state, do NOT return that same current file_id; return the earlier distinct file_id that represents the prior saved version.
- When the user describes an edit that just happened, such as deleting a column, choose the file_id from before that edit.
- If the request references a specific turn number, use the file_id from that turn.
- Use file_id fields when present.
- Use download URLs mentioned in messages when they help recover the correct version.
- If no matching old file version exists, return an error.

Return ONLY valid JSON.

Success format:
{{
    "file_id": "<matching old file_id>",
    "message": "Reverted to the requested file version."
}}

Error format:
{{
    "error": "No matching old file version found in conversation history",
    "message": "No matching old file version found in conversation history"
}}
 
Requested revert target:
{requested_target}

Conversation history:
{history_prompt}
"""

    try:
        llm = OpenAIService()
        user_prompt = "Return the matching older file version as valid JSON only."
        response = await llm.get_chat_completion(system_prompt, user_prompt)
        text = response.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= start:
            text = text[start : end + 1]

        payload = json.loads(text)
        error = payload.get("error")
        if isinstance(error, str) and error:
            message = payload.get("message")
            return {
                "error": error,
                "message": message if isinstance(message, str) and message else error,
            }

        file_id = payload.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            error_message = "No matching old file version found in conversation history"
            return {"error": error_message, "message": error_message}

        download_url = payload.get("download_url", "")
        message = payload.get("message", "Reverted to the requested file version.")
        app_settings = AppSettingsAdapter()
        return {
            "file_id": file_id,
            "download_url": f"{app_settings.FILE_URL}/api/v1/download/{file_id}" if isinstance(file_id, str) and file_id else "",
            "message": message if isinstance(message, str) and message else "Reverted to the requested file version.",
        }
    
    except json.JSONDecodeError:
        error_message = "Failed to parse revert_file response"
        return {"error": error_message, "message": error_message}
    except Exception as exc:
        return {"error": str(exc), "message": "Failed to determine previous file version"}


@tool("revert_file", internal=True)
async def revert_file(revert_instruction: str = "", conversation_history: str = "") -> dict:
    """Revert to an older file version using a user request string and rendered conversation history.

    Args:
        revert_instruction: User request describing which older version to restore.
        conversation_history: Rendered chronological conversation history string.

    Returns:
        {
            "file_id": str,
            "download_url": str,
            "message": str
        }
    """
    return await _revert_file_impl(revert_instruction, conversation_history)
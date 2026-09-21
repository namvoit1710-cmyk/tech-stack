# from __future__ import annotations

# from unittest.mock import AsyncMock

# import pytest

# @pytest.mark.asyncio
# async def test_revert_file_impl_uses_explicit_revert_instruction_and_history(monkeypatch) -> None:
#     from app.layer2_application.features import revert_file as revert_module

#     llm = type("MockLLM", (), {})()
#     llm.get_chat_completion = AsyncMock(
#         return_value='{"file_id": "019e1c2d-db32-7b6a-96ea-93d16a0cdfff_Legit_Data_4.csv", "download_url": "https://example.com/api/v1/download/019e1c2d-db32-7b6a-96ea-93d16a0cdfff_Legit_Data_4.csv", "message": "Reverted to the requested file version."}'
#     )
#     monkeypatch.setattr(revert_module, "OpenAIService", lambda: llm)

#     rendered_history = "\n".join(
#         [
#             "Conversation history:",
#             "- turn 1 | role=user | file_id=019e1c2d-db32-7b6a-96ea-93d16a0cdfff_Legit_Data_4.csv | message=Original file.",
#             "- turn 2 | role=system | file_id=019e1c2f-e321-78a5-bf79-e63da1b047a4_tmp5ny0i31m.csv | message=Done! Download it here: https://example.com/api/v1/download/019e1c2f-e321-78a5-bf79-e63da1b047a4_tmp5ny0i31m.csv",
#             "- turn 3 | role=user | file_id=019e1c2f-e321-78a5-bf79-e63da1b047a4_tmp5ny0i31m.csv | message=Revert to the version from turn 1.",
#         ]
#     )

#     result = await revert_module._revert_file_impl(
#         "Revert to the version from turn 1.",
#         rendered_history,
#     )

#     assert result["file_id"] == "019e1c2d-db32-7b6a-96ea-93d16a0cdfff_Legit_Data_4.csv"
#     assert result["download_url"] == "https://example.com/api/v1/download/019e1c2d-db32-7b6a-96ea-93d16a0cdfff_Legit_Data_4.csv"
#     assert result["message"] == "Reverted to the requested file version."

#     system_prompt, user_prompt = llm.get_chat_completion.await_args.args
#     assert "Return ONLY valid JSON." in system_prompt
#     assert "Requested revert target:" in system_prompt
#     assert "Revert to the version from turn 1." in system_prompt
#     assert "file_id=019e1c2f-e321-78a5-bf79-e63da1b047a4_tmp5ny0i31m.csv" in system_prompt
#     assert user_prompt == "Return the matching older file version as valid JSON only."


# @pytest.mark.asyncio
# async def test_revert_file_impl_uses_default_instruction_when_missing(monkeypatch) -> None:
#     from app.layer2_application.features import revert_file as revert_module

#     llm = type("MockLLM", (), {})()
#     llm.get_chat_completion = AsyncMock(
#         return_value='{"file_id": "old-file.csv", "download_url": "", "message": "Reverted to the requested file version."}'
#     )
#     monkeypatch.setattr(revert_module, "OpenAIService", lambda: llm)

#     rendered_history = "\n".join(
#         [
#             "Conversation history:",
#             "- turn 1 | role=user | file_id=old-file.csv | message=Original file.",
#             "- turn 2 | role=system | file_id=new-file.csv | message=Done! Download it here: https://example.com/api/v1/download/new-file.csv",
#             "- turn 3 | role=user | file_id=new-file.csv | message=Revert to the previous file version.",
#         ]
#     )

#     result = await revert_module._revert_file_impl("", rendered_history)

#     assert result == {
#         "file_id": "old-file.csv",
#         "download_url": "",
#         "message": "Reverted to the requested file version.",
#     }

#     system_prompt, user_prompt = llm.get_chat_completion.await_args.args
#     assert "Select the correct older file version requested by the user from the conversation history." in system_prompt
#     assert user_prompt == "Return the matching older file version as valid JSON only."
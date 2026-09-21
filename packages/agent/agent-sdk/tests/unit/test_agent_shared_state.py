# """Unit tests for agent_shared_state domain entities and methods."""

# import json
# import pytest
# from datetime import datetime

# from agent_sdk.layer1_domain.entities.agent_shared_state import (
#     AgentSharedState,
#     AuditLogEntry,
#     Conversation,
#     GlobalContext,
#     History,
#     Metadata,
#     Task,
# )
# from agent_sdk.layer1_domain.entities.conversation_context import FileObject

# # ── Helpers ──


# def _make_metadata(**overrides) -> Metadata:
#     defaults = {
#         "conversation_id": "conv-001",
#         "checkpoint_id": "CP-01",
#         "current_agent": "OrchestratorAgent",
#     }
#     defaults.update(overrides)
#     return Metadata(**defaults)


# def _make_context(**overrides) -> GlobalContext:
#     defaults = {"user_id": "user-1"}
#     defaults.update(overrides)
#     return GlobalContext(**defaults)


# def _make_task(task_id: str, agent: str = "agent.file", name: str = "Do something", **overrides) -> Task:
#     defaults = {"task_id": task_id, "agent": agent, "name": name}
#     defaults.update(overrides)
#     return Task(**defaults)


# def _make_shared_state(**overrides) -> AgentSharedState:
#     defaults = {
#         "metadata": _make_metadata(),
#         "context": _make_context(),
#     }
#     defaults.update(overrides)
#     return AgentSharedState(**defaults)


# # ── Metadata tests ──


# def test_metadata_defaults():
#     m = _make_metadata()
#     assert m.status == "IN_PROGRESS"
#     assert m.branch == "main"
#     assert m.main_conv_id is None


# def test_metadata_custom_status():
#     m = _make_metadata(status="COMPLETED")
#     assert m.status == "COMPLETED"


# # ── GlobalContext tests ──


# def test_global_context_with_files():
#     ctx = GlobalContext(
#         user_id="user-1",
#         files=[FileObject(file_id="f1"), FileObject(file_id="f2", status="parsed")],
#     )
#     assert len(ctx.files) == 2
#     assert ctx.files[1].status == "parsed"


# def test_global_context_defaults():
#     ctx = _make_context()
#     assert ctx.conversation_context is None
#     assert ctx.files == []
#     assert ctx.error_summary is None


# # ── Task tests ──


# def test_task_alias_fields():
#     t = Task(task_id="s1", agent="agent.file", name="Parse file")
#     assert t.id == "s1"
#     assert t.execute_by == "agent.file"
#     assert t.goal == "Parse file"


# def test_task_defaults():
#     t = _make_task("s1")
#     assert t.status == "pending"
#     assert t.depend_on == []
#     assert t.is_dynamic is False


# def test_task_populate_by_name():
#     t = Task(id="s1", execute_by="agent.file", goal="Parse file")
#     assert t.id == "s1"
#     assert t.execute_by == "agent.file"


# # ── History tests ──


# def test_history_creation():
#     h = History(
#         role="assistant",
#         content="Done",
#         agent_id="File_Agent",
#         conv_id="conv-1",
#         sub_conv_id="sub-1",
#     )
#     assert h.role == "assistant"
#     assert isinstance(h.timestamp, datetime)


# # ── AuditLogEntry tests ──


# def test_audit_log_entry_creation():
#     entry = AuditLogEntry(
#         id="log-001",
#         conv_id="conv-1",
#         checkpoint_id="CP-02",
#         agent_name="File_Agent",
#         action_name="Parse_Excel_Header",
#         status="SUCCESS",
#         details={"rows_parsed": 42},
#     )
#     assert entry.status == "SUCCESS"
#     assert entry.details["rows_parsed"] == 42
#     assert isinstance(entry.created_at, datetime)


# def test_audit_log_entry_details_optional():
#     entry = AuditLogEntry(
#         id="log-002",
#         conv_id="conv-1",
#         checkpoint_id="CP-01",
#         agent_name="OrchestratorAgent",
#         action_name="Route",
#         status="SUCCESS",
#     )
#     assert entry.details is None


# # ── AgentSharedState method tests ──


# def test_update_metadata():
#     state = _make_shared_state()
#     new_meta = _make_metadata(checkpoint_id="CP-02", current_agent="File_Agent")
#     state.update_metadata(new_meta)
#     assert state.metadata.checkpoint_id == "CP-02"
#     assert state.metadata.current_agent == "File_Agent"


# def test_update_context():
#     state = _make_shared_state()
#     new_ctx = _make_context(conversation_context="Create CR from file")
#     state.update_context(new_ctx)
#     assert state.context.conversation_context == "Create CR from file"


# def test_update_executions_replaces_list():
#     state = _make_shared_state(executions=[_make_task("s1")])
#     new_tasks = [_make_task("s2"), _make_task("s3")]
#     state.update_executions(new_tasks)
#     assert len(state.executions) == 2
#     assert state.executions[0].id == "s2"


# def test_upsert_task_adds_new():
#     state = _make_shared_state(executions=[_make_task("s1")])
#     state.upsert_task(_make_task("s2"))
#     assert len(state.executions) == 2
#     assert state.executions[1].id == "s2"


# def test_upsert_task_replaces_existing():
#     state = _make_shared_state(executions=[_make_task("s1", name="Old goal")])
#     state.upsert_task(_make_task("s1", name="New goal"))
#     assert len(state.executions) == 1
#     assert state.executions[0].goal == "New goal"


# def test_update_task_partial_status():
#     state = _make_shared_state(executions=[
#         _make_task("s1", name="Parse"),
#         _make_task("s2", name="Validate"),
#     ])
#     state.update_task("s1", status="success")
#     assert state.executions[0].status == "success"
#     assert state.executions[0].goal == "Parse"  # unchanged
#     assert state.executions[1].status == "pending"  # other task untouched


# def test_update_task_multiple_fields():
#     state = _make_shared_state(executions=[_make_task("s1", name="Parse")])
#     state.update_task("s1", status="error", error_detail="Timeout", output_data={"partial": True})
#     assert state.executions[0].status == "error"
#     assert state.executions[0].error_detail == "Timeout"
#     assert state.executions[0].output_data == {"partial": True}


# def test_update_task_not_found_raises():
#     state = _make_shared_state(executions=[_make_task("s1")])
#     with pytest.raises(ValueError, match="Task 'nonexistent' not found"):
#         state.update_task("nonexistent", status="error")


# def test_update_task_invalid_field_raises():
#     state = _make_shared_state(executions=[_make_task("s1")])
#     with pytest.raises(ValueError, match="Unknown Task fields"):
#         state.update_task("s1", bogus="value")


# def test_add_history():
#     state = _make_shared_state()
#     h = History(role="user", content="Hello", conv_id="c1", sub_conv_id="s1")
#     state.add_history(h)
#     assert len(state.histories) == 1
#     assert state.histories[0].content == "Hello"


# # ── Conversation tests ──


# def test_conversation_creation():
#     conv = Conversation(
#         conv_id="conv-001",
#         orchestrator="OrchestratorAgent",
#         user_id="user-1",
#         shared_state=_make_shared_state(),
#     )
#     assert conv.conv_id == "conv-001"
#     assert conv.shared_state.metadata.conversation_id == "conv-001"


# def test_conversation_serialization_roundtrip():
#     state = _make_shared_state(
#         executions=[_make_task("s1"), _make_task("s2")],
#     )
#     conv = Conversation(
#         conv_id="conv-001",
#         orchestrator="OrchestratorAgent",
#         user_id="user-1",
#         shared_state=state,
#     )
#     data = json.loads(conv.model_dump_json(by_alias=True))
#     restored = Conversation(**data)
#     assert restored.conv_id == conv.conv_id
#     assert len(restored.shared_state.executions) == 2
#     assert restored.shared_state.executions[0].id == "s1"

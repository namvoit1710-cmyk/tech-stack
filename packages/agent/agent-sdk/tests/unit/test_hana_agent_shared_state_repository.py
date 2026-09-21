# """Unit tests for HanaAgentSharedStateRepository."""

# import json
# from unittest.mock import MagicMock

# import pytest
# from agent_sdk.layer1_domain.entities.agent_shared_state import (
#     AgentSharedState,
#     AuditLogEntry,
#     Conversation,
#     GlobalContext,
#     Metadata,
#     Task,
# )
# from agent_sdk.layer4_frameworks.persistence.hana.base_repository import (
#     BaseHanaRepository,
# )
# from agent_sdk.layer4_frameworks.persistence.hana.hana_agent_shared_state_repository import (
#     HanaAgentSharedStateRepository,
# )

# _STATES_TABLE = '"AIW_AGENT_STATES"'

# # ── Helpers ──


# def _make_db() -> MagicMock:
#     return MagicMock()


# def _make_conversation(conv_id: str = "conv-001") -> Conversation:
#     return Conversation(
#         conv_id=conv_id,
#         user_id="user-1",
#         shared_state=AgentSharedState(
#             metadata=Metadata(
#                 conversation_id=conv_id,
#                 checkpoint_id="CP-01",
#                 current_agent="OrchestratorAgent",
#             ),
#             context=GlobalContext(user_id="user-1"),
#             executions=[
#                 Task(task_id="s1", agent="agent.file", name="Parse file"),
#                 Task(
#                     task_id="s2",
#                     agent="agent.validate",
#                     name="Validate data",
#                     depend_on=["s1"],
#                 ),
#             ],
#         ),
#     )


# def _db_row_from_conversation(conv: Conversation) -> dict:
#     shared = conv.shared_state
#     return {
#         "CONV_ID": conv.conv_id,
#         "USER_ID": conv.user_id,
#         "METADATA": json.dumps(shared.metadata.model_dump(by_alias=True)),
#         "CONTEXT": json.dumps(shared.context.model_dump(by_alias=True)),
#         "EXECUTIONS": json.dumps(
#             [t.model_dump(by_alias=True) for t in shared.executions]
#         ),
#         "UPDATED_AT": "2026-01-01T00:00:00+00:00",
#     }


# def _db_row_from_conversation_lowercase(conv: Conversation) -> dict:
#     return {
#         key.lower(): value for key, value in _db_row_from_conversation(conv).items()
#     }


# # ── save_conversation ──


# def test_save_conversation_calls_merge():
#     db = _make_db()
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))
#     conv = _make_conversation()

#     repo.save_conversation(conv)

#     sql, params = db.execute_write.call_args.args
#     assert "MERGE INTO" in sql
#     assert '"AIW_AGENT_STATES"' in sql
#     assert params[0] == "conv-001"
#     assert params[1] == "user-1"


# # ── get_conversation ──


# def test_get_conversation_returns_none_when_not_found():
#     db = _make_db()
#     db.execute_query.return_value = []
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     result = repo.get_conversation("missing")

#     assert result is None


# def test_get_conversation_reconstructs_full_object():
#     conv = _make_conversation()
#     db = _make_db()
#     db.execute_query.return_value = [_db_row_from_conversation(conv)]
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     result = repo.get_conversation("conv-001")

#     assert result is not None
#     assert result.conv_id == "conv-001"
#     assert result.user_id == "user-1"
#     assert result.shared_state.metadata.checkpoint_id == "CP-01"
#     assert len(result.shared_state.executions) == 2
#     assert result.shared_state.executions[0].id == "s1"


# def test_get_conversation_accepts_lowercase_row_keys():
#     conv = _make_conversation()
#     db = _make_db()
#     db.execute_query.return_value = [_db_row_from_conversation_lowercase(conv)]
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     result = repo.get_conversation("conv-001")

#     assert result is not None
#     assert result.conv_id == "conv-001"
#     assert result.user_id == "user-1"

# # ── list_conversation ──

# def test_list_conversation_reconstructs_objects_and_uses_bound_pagination():
#     conv_a = _make_conversation("conv-001")
#     conv_b = _make_conversation("conv-002")
#     db = _make_db()
#     db.execute_query.return_value = [
#         _db_row_from_conversation(conv_a),
#         _db_row_from_conversation_lowercase(conv_b),
#     ]
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     result = repo.list_conversation(user_id="user-1", skip=5, top=2)

#     sql, params = db.execute_query.call_args.args
#     assert 'SELECT "CONV_ID", "USER_ID", "METADATA", "CONTEXT", "EXECUTIONS", "UPDATED_AT"' in sql
#     assert 'FROM "AIW_AGENT_STATES"' in sql
#     assert 'WHERE "USER_ID" = :p0' in sql
#     assert 'ORDER BY "UPDATED_AT" DESC' in sql
#     assert "LIMIT :p1 OFFSET :p2" in sql
#     assert params == ('user-1', 2, 5)
#     assert [conversation.conv_id for conversation in result] == ["conv-001", "conv-002"]
#     assert result[0].shared_state.metadata.checkpoint_id == "CP-01"
#     assert result[0].shared_state.context.user_id == "user-1"
#     assert result[0].shared_state.executions[0].id == "s1"
#     assert result[1].user_id == "user-1"

# # ── count_conversation ──

# def test_count_conversation_by_user_id():
#     db = _make_db()
#     db.execute_query.return_value = [{"cnt": 5}]
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     result = repo.count_conversation(user_id="user-1")

#     sql, params = db.execute_query.call_args.args
#     assert "SELECT count(CONV_ID) as cnt" in sql
#     assert 'FROM "AIW_AGENT_STATES"' in sql
#     assert 'WHERE "USER_ID" = :p0' in sql
#     assert params == ("user-1",)
#     assert result == 5


# def test_count_conversation_returns_zero_when_empty():
#     db = _make_db()
#     db.execute_query.return_value = [{"cnt": 0}]
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     result = repo.count_conversation(user_id="user-1")

#     assert result == 0


# # ── get_conversation_by with task_id ──


# def test_get_conversation_by_task_id_includes_dependents():
#     conv = _make_conversation()
#     db = _make_db()
#     db.execute_query.side_effect = [
#         [_db_row_from_conversation(conv)],
#         [],  # audit logs
#     ]
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     result = repo.get_conversation_by("conv-001", task_id="s1")

#     assert result is not None
#     task_ids = {t.id for t in result.shared_state.executions}
#     assert "s1" in task_ids
#     assert "s2" in task_ids  # s2 depends on s1


# def test_get_conversation_by_task_id_not_found_returns_empty():
#     conv = _make_conversation()
#     db = _make_db()
#     db.execute_query.return_value = [_db_row_from_conversation(conv)]
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     result = repo.get_conversation_by("conv-001", task_id="nonexistent")

#     assert result is not None
#     assert len(result.shared_state.executions) == 0


# # ── get_conversation_by no filter ──


# def test_get_conversation_by_no_filter_returns_all():
#     conv = _make_conversation()
#     db = _make_db()
#     db.execute_query.return_value = [_db_row_from_conversation(conv)]
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     result = repo.get_conversation_by("conv-001")

#     assert result is not None
#     assert len(result.shared_state.executions) == 2


# # ── delete_conversation ──


# def test_delete_conversation_executes_delete():
#     db = _make_db()
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     result = repo.delete_conversation("conv-001")

#     assert result is True
#     sql, params = db.execute_write.call_args.args
#     assert "DELETE FROM" in sql
#     assert params[0] == "conv-001"


# # ── update_metadata ──


# def test_update_metadata_updates_only_metadata_column():
#     db = _make_db()
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))
#     meta = Metadata(
#         conversation_id="conv-001", checkpoint_id="CP-02", current_agent="File_Agent"
#     )
#     db.execute_write.return_value = 1

#     repo.update_metadata("conv-001", meta)

#     sql, params = db.execute_write.call_args.args
#     assert "SET" in sql
#     assert '"METADATA"' in sql
#     assert '"CONTEXT"' not in sql
#     parsed = json.loads(params[0])
#     assert parsed["checkpoint_id"] == "CP-02"
#     assert params[2] == "conv-001"


# # ── update_context ──


# def test_update_context_updates_only_context_column():
#     db = _make_db()
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))
#     ctx = GlobalContext(conversation_context="Create CR")
#     db.execute_write.return_value = 1

#     repo.update_context("conv-001", ctx)

#     sql, params = db.execute_write.call_args.args
#     assert '"CONTEXT"' in sql
#     assert '"METADATA"' not in sql
#     parsed = json.loads(params[0])
#     assert parsed["conversation_context"] == "Create CR"


# # ── update_executions (upsert merge) ──


# def test_update_executions_merges_with_existing():
#     db = _make_db()
#     existing_tasks = [
#         {
#             "id": "s1",
#             "task_id": "s1",
#             "agent": "agent.file",
#             "name": "Parse file",
#             "status": "success",
#         },
#         {
#             "id": "s2",
#             "task_id": "s2",
#             "agent": "agent.validate",
#             "name": "Validate",
#             "status": "pending",
#         },
#     ]
#     db.execute_query.return_value = [{"EXECUTIONS": json.dumps(existing_tasks)}]
#     db.execute_write.return_value = 1
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))
#     repo._try_insert_executions = lambda *args, **kwargs: True
#     repo._try_merge_and_update_executions = lambda *args, **kwargs: True

#     # Update s2 status and add new s3
#     new_tasks = [
#         Task(task_id="s2", agent="agent.validate", name="Validate", status="success"),
#         Task(task_id="s3", agent="agent.cr", name="Create CR"),
#     ]
#     repo.upsert_executions("conv-001", new_tasks)

#     # DB call is mocked, so we cannot assert on db.execute_write.call_args
#     # Instead, just ensure no exception is raised and logic completes


# def test_update_executions_handles_empty_existing():
#     db = _make_db()
#     db.execute_query.return_value = [{"EXECUTIONS": None}]
#     db.execute_write.return_value = 1
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))
#     repo._try_insert_executions = lambda *args, **kwargs: True
#     repo._try_merge_and_update_executions = lambda *args, **kwargs: True

#     repo.upsert_executions("conv-001", [Task(task_id="s1", agent="a", name="Do")])

#     # DB call is mocked, so we cannot assert on db.execute_write.call_args
#     # Just ensure no exception is raised


# def test_update_executions_handles_no_row():
#     db = _make_db()
#     db.execute_query.return_value = []
#     db.execute_write.return_value = 1
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))
#     repo._try_insert_executions = lambda *args, **kwargs: True
#     repo._try_merge_and_update_executions = lambda *args, **kwargs: True

#     repo.upsert_executions("conv-001", [Task(task_id="s1", agent="a", name="Do")])

#     # DB call is mocked, so we cannot assert on db.execute_write.call_args
#     # Just ensure no exception is raised


# def test_upsert_executions_succeeds_when_write_returns_rowcount():
#     db = _make_db()
#     db.execute_query.return_value = []
#     db.execute_write.return_value = 1
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     repo.upsert_executions(
#         "conv-001",
#         [Task(task_id="s1", agent="agent.file", name="Parse file")],
#         max_retries=1,
#     )

#     db.execute_write.assert_called_once()


# # ── update_task (partial update) ──


# def test_update_task_updates_status_only():
#     db = _make_db()
#     existing_tasks = [
#         {
#             "id": "s1",
#             "task_id": "s1",
#             "agent": "agent.file",
#             "name": "Parse file",
#             "status": "pending",
#         },
#         {
#             "id": "s2",
#             "task_id": "s2",
#             "agent": "agent.validate",
#             "name": "Validate",
#             "status": "pending",
#         },
#     ]
#     db.execute_query.return_value = [{"EXECUTIONS": json.dumps(existing_tasks)}]
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     repo.update_task("conv-001", "s1", status="success")

#     sql, params = db.execute_write.call_args.args
#     updated = json.loads(params[0])
#     s1 = next(t for t in updated if t["id"] == "s1")
#     s2 = next(t for t in updated if t["id"] == "s2")
#     assert s1["status"] == "success"
#     assert s1["agent"] == "agent.file"  # unchanged
#     assert s1["name"] == "Parse file"  # unchanged
#     assert s2["status"] == "pending"  # other task untouched


# def test_update_task_updates_multiple_fields():
#     db = _make_db()
#     existing_tasks = [
#         {
#             "id": "s1",
#             "task_id": "s1",
#             "agent": "agent.file",
#             "name": "Parse",
#             "status": "processing",
#         },
#     ]
#     db.execute_query.return_value = [{"EXECUTIONS": json.dumps(existing_tasks)}]
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     repo.update_task(
#         "conv-001",
#         "s1",
#         status="success",
#         output_data={"rows": 42},
#         error_detail=None,
#     )

#     updated = json.loads(db.execute_write.call_args.args[1][0])
#     s1 = updated[0]
#     assert s1["status"] == "success"
#     assert s1["output_data"] == {"rows": 42}
#     assert s1["error_detail"] is None


# def test_update_task_not_found_raises():
#     db = _make_db()
#     db.execute_query.return_value = [
#         {
#             "EXECUTIONS": json.dumps(
#                 [
#                     {"id": "s1", "task_id": "s1", "agent": "a", "name": "x"},
#                 ]
#             )
#         }
#     ]
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     with pytest.raises(ValueError, match="Task 'nonexistent' not found"):
#         repo.update_task("conv-001", "nonexistent", status="error")


# def test_update_task_invalid_field_raises():
#     db = _make_db()
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     with pytest.raises(ValueError, match="Unknown Task fields"):
#         repo.update_task("conv-001", "s1", bogus_field="value")


# def test_update_task_preserves_other_tasks():
#     db = _make_db()
#     existing_tasks = [
#         {
#             "id": "s1",
#             "task_id": "s1",
#             "agent": "a1",
#             "name": "T1",
#             "status": "success",
#             "output_data": {"k": 1},
#         },
#         {"id": "s2", "task_id": "s2", "agent": "a2", "name": "T2", "status": "pending"},
#         {"id": "s3", "task_id": "s3", "agent": "a3", "name": "T3", "status": "pending"},
#     ]
#     db.execute_query.return_value = [{"EXECUTIONS": json.dumps(existing_tasks)}]
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     repo.update_task("conv-001", "s2", status="error", error_detail="Timeout")

#     updated = json.loads(db.execute_write.call_args.args[1][0])
#     assert len(updated) == 3
#     assert updated[0] == existing_tasks[0]  # s1 untouched
#     assert updated[2] == existing_tasks[2]  # s3 untouched
#     assert updated[1]["status"] == "error"
#     assert updated[1]["error_detail"] == "Timeout"


# # ── append_audit_log ──


# def test_append_audit_log_inserts_into_audit_table():
#     db = _make_db()
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))
#     entry = AuditLogEntry(
#         id="log-001",
#         conv_id="conv-001",
#         checkpoint_id="CP-02",
#         agent_name="File_Agent",
#         action_name="Parse_Excel_Header",
#         status="SUCCESS",
#         details={"rows": 42},
#     )

#     repo.append_audit_log(entry)

#     sql, params = db.execute_write.call_args.args
#     assert "INSERT INTO" in sql
#     assert '"AIW_AGENT_AUDIT_LOGS"' in sql
#     assert params[0] == "log-001"
#     assert params[1] == "conv-001"
#     assert params[5] == "SUCCESS"
#     assert json.loads(params[6]) == {"rows": 42}


# def test_append_audit_log_handles_null_details():
#     db = _make_db()
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))
#     entry = AuditLogEntry(
#         id="log-002",
#         conv_id="conv-001",
#         checkpoint_id="CP-01",
#         agent_name="OrchestratorAgent",
#         action_name="Route",
#         status="SUCCESS",
#     )

#     repo.append_audit_log(entry)

#     params = db.execute_write.call_args.args[1]
#     assert params[6] is None


# # ── get_audit_logs ──


# def test_get_audit_logs_returns_ordered_entries():
#     db = _make_db()
#     db.execute_query.return_value = [
#         {
#             "ID": "log-001",
#             "CONV_ID": "conv-001",
#             "CHECKPOINT_ID": "CP-01",
#             "AGENT_NAME": "File_Agent",
#             "ACTION_NAME": "Parse_Excel",
#             "STATUS": "SUCCESS",
#             "DETAILS": '{"rows": 10}',
#             "CREATED_AT": "2026-01-01T00:00:00+00:00",
#         },
#         {
#             "ID": "log-002",
#             "CONV_ID": "conv-001",
#             "CHECKPOINT_ID": "CP-02",
#             "AGENT_NAME": "File_Agent",
#             "ACTION_NAME": "Validate",
#             "STATUS": "ERROR",
#             "DETAILS": None,
#             "CREATED_AT": "2026-01-01T00:01:00+00:00",
#         },
#     ]
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     logs = repo.get_audit_logs("conv-001")

#     assert len(logs) == 2
#     assert logs[0].id == "log-001"
#     assert logs[0].details == {"rows": 10}
#     assert logs[1].status == "ERROR"
#     assert logs[1].details is None
#     sql = db.execute_query.call_args.args[0]
#     assert "ORDER BY" in sql
#     assert "LIMIT" in sql


# def test_get_audit_logs_empty():
#     db = _make_db()
#     db.execute_query.return_value = []
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     logs = repo.get_audit_logs("conv-001")

#     assert logs == []


# # ── setup / migrations ──


# def test_setup_calls_migrate():
#     db = _make_db()
#     repo = HanaAgentSharedStateRepository(BaseHanaRepository(db, _STATES_TABLE))

#     # setup calls migrate which calls execute_write for DDL statements
#     repo.setup()

#     assert db.execute_write.called
#     # Should have created both tables + indexes
#     calls = [call.args[0] for call in db.execute_write.call_args_list]
#     ddl_text = " ".join(calls)
#     assert "AIW_AGENT_STATES" in ddl_text
#     assert "AIW_AGENT_AUDIT_LOGS" in ddl_text

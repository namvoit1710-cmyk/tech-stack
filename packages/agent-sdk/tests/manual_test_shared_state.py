"""Manual smoke test for HanaAgentSharedStateRepository composition pattern."""

import json
from unittest.mock import MagicMock

from agent_sdk.layer1_domain.entities.agent_shared_state import (
    AgentSharedState,
    AuditLogEntry,
    Conversation,
    GlobalContext,
    Metadata,
    Task,
)
from agent_sdk.layer2_application.interfaces.agent_shared_state_repository import (
    IAgentSharedStateRepository,
)
from agent_sdk.layer4_frameworks.persistence.hana.base_repository import (
    BaseHanaRepository,
)
from agent_sdk.layer4_frameworks.persistence.hana.hana_agent_shared_state_repository import (
    HanaAgentSharedStateRepository,
)

_STATES_TABLE = '"AIW_AGENT_STATES"'


def _make_db() -> MagicMock:
    return MagicMock()


def _make_conversation(conv_id: str = "conv-001") -> Conversation:
    return Conversation(
        conv_id=conv_id,
        user_id="user-1",
        shared_state=AgentSharedState(
            metadata=Metadata(
                conversation_id=conv_id,
                checkpoint_id="CP-01",
                current_agent="OrchestratorAgent",
            ),
            context=GlobalContext(user_id="user-1"),
            executions=[
                Task(task_id="s1", agent="agent.file", name="Parse file"),
                Task(
                    task_id="s2",
                    agent="agent.validate",
                    name="Validate data",
                    depend_on=["s1"],
                ),
            ],
        ),
    )


def _db_row_from_conversation(conv: Conversation) -> dict:
    shared = conv.shared_state
    return {
        "CONV_ID": conv.conv_id,
        "USER_ID": conv.user_id,
        "METADATA": json.dumps(shared.metadata.model_dump(by_alias=True)),
        "CONTEXT": json.dumps(shared.context.model_dump(by_alias=True)),
        "EXECUTIONS": json.dumps(
            [t.model_dump(by_alias=True) for t in shared.executions]
        ),
        "UPDATED_AT": "2026-01-01T00:00:00+00:00",
    }


def main() -> None:
    db = _make_db()
    base_repo = BaseHanaRepository(db, _STATES_TABLE)
    repo = HanaAgentSharedStateRepository(repo=base_repo)

    # 1. Verify implements IAgentSharedStateRepository (class hierarchy check)
    assert (
        IAgentSharedStateRepository in HanaAgentSharedStateRepository.__mro__
    ), "HanaAgentSharedStateRepository should subclass IAgentSharedStateRepository"
    print(
        "[PASS] IAgentSharedStateRepository in HanaAgentSharedStateRepository.__mro__"
    )

    # 2. Verify composition — _repo is BaseHanaRepository, not inherited
    assert hasattr(repo, "_repo"), "Should have _repo attribute (composition)"
    assert isinstance(repo._repo, BaseHanaRepository)
    assert not isinstance(
        repo, BaseHanaRepository
    ), "Should NOT inherit from BaseHanaRepository"
    print("[PASS] Composition: repo._repo is BaseHanaRepository, not inherited")

    # 3. Test save_conversation delegates to db
    conv = _make_conversation()
    repo.save_conversation(conv)
    assert db.execute_write.called, "save_conversation should call db.execute_write"
    sql = db.execute_write.call_args.args[0]
    assert "MERGE INTO" in sql, f"Expected MERGE INTO, got: {sql}"
    print("[PASS] save_conversation → MERGE INTO executed")

    # 4. Test get_conversation round-trip
    db.reset_mock()
    row = _db_row_from_conversation(conv)
    db.execute_query.return_value = [row]

    result = repo.get_conversation("conv-001")
    assert result is not None
    assert result.conv_id == "conv-001"
    assert result.user_id == "user-1"
    assert len(result.shared_state.executions) == 2
    assert result.shared_state.executions[0].id == "s1"
    assert result.shared_state.executions[1].id == "s2"
    print(
        f"[PASS] get_conversation → returned {len(result.shared_state.executions)} tasks"
    )

    # 5. Test get_conversation returns None for missing
    db.execute_query.return_value = []
    assert repo.get_conversation("nonexistent") is None
    print("[PASS] get_conversation → None for missing conv_id")

    # 6. Test delete_conversation
    db.reset_mock()
    result = repo.delete_conversation("conv-001")
    assert result is True
    sql = db.execute_write.call_args.args[0]
    assert "DELETE" in sql
    print("[PASS] delete_conversation → DELETE executed")

    # 7. Test get_metadata
    db.reset_mock()
    db.execute_query.return_value = [
        {"METADATA": json.dumps(conv.shared_state.metadata.model_dump(by_alias=True))}
    ]
    meta = repo.get_metadata("conv-001")
    assert meta is not None
    assert meta.conversation_id == "conv-001"
    print(f"[PASS] get_metadata → conversation_id={meta.conversation_id}")

    # 8. Test update_metadata
    db.reset_mock()
    repo.update_metadata("conv-001", conv.shared_state.metadata)
    sql = db.execute_write.call_args.args[0]
    assert "UPDATE" in sql and "METADATA" in sql
    print("[PASS] update_metadata → UPDATE executed")

    # 9. Test upsert_executions (insert path)
    db.reset_mock()
    db.execute_query.return_value = []  # no existing row
    db.execute_write.return_value = 1  # insert succeeds
    new_task = Task(task_id="s3", agent="agent.new", name="New task")
    repo.upsert_executions("conv-002", [new_task])
    sql = db.execute_write.call_args.args[0]
    assert "INSERT" in sql
    print("[PASS] upsert_executions (insert path) → INSERT executed")

    # 10. Test get_task
    db.reset_mock()
    db.execute_query.return_value = [
        {
            "EXECUTIONS": json.dumps(
                [t.model_dump(by_alias=True) for t in conv.shared_state.executions]
            )
        }
    ]
    task = repo.get_task("conv-001", "s2")
    assert task is not None
    assert task.id == "s2"
    assert task.goal == "Validate data"
    print(f"[PASS] get_task → id={task.id}, goal={task.goal}")

    # 11. Test append_audit_log
    db.reset_mock()
    from datetime import datetime, timezone

    entry = AuditLogEntry(
        id="log-1",
        conv_id="conv-001",
        checkpoint_id="cp-1",
        agent_name="test-agent",
        action_name="test-action",
        status="SUCCESS",
        details={"key": "value"},
        created_at=datetime.now(timezone.utc),
    )
    repo.append_audit_log(entry)
    sql = db.execute_write.call_args.args[0]
    assert "INSERT INTO" in sql and "AUDIT" in sql
    print("[PASS] append_audit_log → INSERT executed")

    print("\n" + "=" * 50)
    print("All 11 tests passed!")
    print("=" * 50)


if __name__ == "__main__":
    main()

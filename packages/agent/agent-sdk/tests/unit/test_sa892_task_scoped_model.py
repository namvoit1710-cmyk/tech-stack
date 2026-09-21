"""SA-892 (task-scoped stop): agent-sdk model surface.

The per-task stop mechanism needs (a) a CANCELLED terminal Task status and (b) the
task→message link. Under the conversation_context design that link is NOT a field
on Task — it lives in ``conversation_context`` (each ``MessageHistory`` owns the
list of its ``TaskHistory``, written by ``upsert_task_histories`` at plan time), so
a Stop scopes the cancel via that index and a late result is dropped by looking up
ITS OWN task's status.
"""

from agent_sdk.layer1_domain.entities.agent_shared_state import Task, TaskStatus
from agent_sdk.layer1_domain.entities.conversation_context import (
    MessageHistory,
    TaskHistory,
)


def test_task_status_cancelled_exists():
    assert TaskStatus.CANCELLED == "cancelled"
    assert TaskStatus.CANCELLED.value == "cancelled"


def test_task_has_no_message_id_field():
    # The task→message link was moved out of Task into conversation_context.
    task = Task(task_id="t1", agent="agent.file", name="do a thing")
    assert "message_id" not in Task.model_fields
    assert not hasattr(task, "message_id")


def test_task_ignores_legacy_message_id_on_load():
    # Persisted rows written before the field was removed still carry a
    # ``message_id`` key in EXECUTIONS JSON; deserialization must ignore it
    # (extra='ignore') rather than raise, so old conversations still load.
    task = Task(
        task_id="t1", agent="agent.file", name="x", message_id="legacy-msg"
    )
    assert not hasattr(task, "message_id")
    assert "message_id" not in task.model_dump(by_alias=True)


def test_task_message_link_lives_in_conversation_context():
    # The authoritative message→task_ids index: a MessageHistory owns TaskHistory.
    message = MessageHistory(
        message_id="msg-42",
        message="do a thing",
        tasks=[TaskHistory(task_id="t1", knowledge=[]), TaskHistory(task_id="t2")],
    )
    assert {th.task_id for th in message.tasks} == {"t1", "t2"}


def test_task_can_be_set_cancelled():
    task = Task(task_id="t1", agent="agent.file", name="x", status=TaskStatus.PROCESSING)
    task.status = TaskStatus.CANCELLED
    assert task.status == TaskStatus.CANCELLED

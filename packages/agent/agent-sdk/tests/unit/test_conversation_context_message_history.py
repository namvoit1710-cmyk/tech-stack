"""Unit spec — layer1_domain MessageHistory (de)serialization.

Gap-fill sweep 2026-07-02 (unit-smith). conversation_context.py sat at 82%;
the uncovered lines (53, 57-73) are MessageHistory.to_json / from_json, which
had no dedicated test. Cases grounded in source
``agent_sdk/layer1_domain/entities/conversation_context.py`` — note that
from_json rebuilds message_id/message/tasks(+knowledge) but does NOT restore
`files` (asserted below as observed behaviour, not intended contract).

Five case types: happy · edge · invalid input · boundary · failure path.
"""

import json

import pytest

from agent_sdk.layer1_domain.entities.conversation_context import (
    FileObject,
    FileStatus,
    KnowledgeItem,
    MessageHistory,
    TaskHistory,
)


# ── happy path: round-trip with tasks + knowledge ──────────────────────────
def test_to_json_then_from_json_round_trips_tasks_and_knowledge():
    original = MessageHistory(
        message_id="m1",
        message="hello",
        tasks=[
            TaskHistory(
                task_id="t1",
                knowledge=[KnowledgeItem(document_id="d1", summary="s1")],
            )
        ],
    )
    restored = MessageHistory.from_json(original.to_json())
    assert restored.message_id == "m1"
    assert restored.message == "hello"
    assert len(restored.tasks) == 1
    assert restored.tasks[0].task_id == "t1"
    assert restored.tasks[0].knowledge[0].document_id == "d1"
    assert restored.tasks[0].knowledge[0].summary == "s1"


def test_to_json_is_valid_json_with_expected_keys():
    mh = MessageHistory(message_id="m2", message="hi")
    payload = json.loads(mh.to_json())
    assert payload["message_id"] == "m2"
    assert payload["message"] == "hi"
    assert payload["tasks"] == []


# ── edge: unicode preserved (ensure_ascii=False in source) ─────────────────
def test_to_json_preserves_unicode():
    mh = MessageHistory(message_id="m3", message="héllo · 世界")
    payload = mh.to_json()
    assert "héllo · 世界" in payload  # not escaped to \uXXXX


# ── boundary: empty tasks list ─────────────────────────────────────────────
def test_from_json_with_no_tasks_yields_empty_list():
    raw = json.dumps({"message_id": "m4", "message": "x"})
    restored = MessageHistory.from_json(raw)
    assert restored.tasks == []


# ── observed behaviour: files are NOT restored by from_json ────────────────
def test_from_json_does_not_restore_files_field():
    # Source: from_json rebuilds only message_id/message/tasks; files defaults to [].
    original = MessageHistory(
        message_id="m5",
        message="with-file",
        files=[FileObject(file_id="f1", status=FileStatus.PARSED)],
    )
    restored = MessageHistory.from_json(original.to_json())
    assert restored.files == []  # dropped on the round-trip (documented gap)


# ── invalid input: missing required key raises KeyError ────────────────────
def test_from_json_missing_message_id_raises_keyerror():
    # Source: data["message_id"] direct index -> KeyError when absent.
    raw = json.dumps({"message": "no id"})
    with pytest.raises(KeyError):
        MessageHistory.from_json(raw)


# ── failure path: malformed JSON raises JSONDecodeError ────────────────────
def test_from_json_malformed_string_raises():
    # Source: json.loads(value) is uncaught here.
    with pytest.raises(json.JSONDecodeError):
        MessageHistory.from_json("{not json")

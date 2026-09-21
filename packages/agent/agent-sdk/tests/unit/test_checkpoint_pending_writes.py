"""Tests for HanaCheckpointSaver pending_writes loading.

Verifies that get_tuple() and list() populate pending_writes from
AIW_FLOW_CHECKPOINT_WRITES instead of returning None.
"""

from unittest.mock import MagicMock

from agent_sdk.layer4_frameworks.persistence.checkpoint_store import HanaCheckpointSaver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(checkpoint_rows, writes_rows):
    """Return a mock db whose execute_query returns checkpoint_rows then writes_rows."""
    mock_db = MagicMock()
    mock_db.execute_query.side_effect = [checkpoint_rows, writes_rows]
    return mock_db


# ---------------------------------------------------------------------------
# get_tuple – pending_writes must be populated
# ---------------------------------------------------------------------------


def test_get_tuple_includes_pending_writes():
    """get_tuple must query AIW_FLOW_CHECKPOINT_WRITES and populate pending_writes."""
    mock_db = MagicMock()
    saver = HanaCheckpointSaver(db=mock_db)
    checkpoint_rows = [
        {
            "checkpoint_data": saver._serde_pack(
                {
                    "v": 1,
                    "channel_values": {},
                    "channel_versions": {},
                    "versions_seen": {},
                    "pending_sends": [],
                    "id": "cp1",
                }
            ),
            "metadata": saver._serde_pack(
                {"source": "loop", "step": 1, "writes": None, "parents": {}}
            ),
            "parent_config": None,
            "thread_ts": "2024-01-01T00:00:00+00:00",
            "checkpoint_id": "t1:2024-01-01T00:00:00+00:00",
        }
    ]
    writes_rows = [
        {"task_id": "task1", "channel": "messages", "value": saver._serde_pack([1, 2])},
    ]
    mock_db.execute_query.side_effect = [checkpoint_rows, writes_rows]
    config = {"configurable": {"thread_id": "t1"}}
    result = saver.get_tuple(config)

    assert result is not None
    assert result.pending_writes is not None
    assert len(result.pending_writes) > 0
    assert result.pending_writes[0][0] == "task1"
    assert result.pending_writes[0][1] == "messages"


def test_get_tuple_pending_writes_empty_when_no_writes():
    """get_tuple must return empty list (not None) when no pending writes exist."""
    mock_db = MagicMock()
    saver = HanaCheckpointSaver(db=mock_db)
    checkpoint_rows = [
        {
            "checkpoint_data": saver._serde_pack(
                {
                    "v": 1,
                    "channel_values": {},
                    "channel_versions": {},
                    "versions_seen": {},
                    "pending_sends": [],
                    "id": "cp1",
                }
            ),
            "metadata": saver._serde_pack({}),
            "parent_config": None,
            "thread_ts": "2024-01-01T00:00:00+00:00",
            "checkpoint_id": "t1:2024-01-01T00:00:00+00:00",
        }
    ]
    mock_db.execute_query.side_effect = [checkpoint_rows, []]
    config = {"configurable": {"thread_id": "t1"}}
    result = saver.get_tuple(config)

    assert result is not None
    assert result.pending_writes is not None
    assert len(result.pending_writes) == 0


def test_get_tuple_returns_none_when_no_checkpoint():
    """get_tuple must return None when no checkpoint exists (no writes query)."""
    mock_db = MagicMock()
    mock_db.execute_query.return_value = []

    saver = HanaCheckpointSaver(db=mock_db)
    config = {"configurable": {"thread_id": "t1"}}
    result = saver.get_tuple(config)

    assert result is None


# ---------------------------------------------------------------------------
# list – pending_writes must be populated for each tuple
# ---------------------------------------------------------------------------


def test_list_includes_pending_writes():
    """list must include pending_writes for each checkpoint tuple."""
    mock_db = MagicMock()
    saver = HanaCheckpointSaver(db=mock_db)
    checkpoint_rows = [
        {
            "checkpoint_data": saver._serde_pack(
                {
                    "v": 1,
                    "channel_values": {},
                    "channel_versions": {},
                    "versions_seen": {},
                    "pending_sends": [],
                    "id": "cp1",
                }
            ),
            "metadata": saver._serde_pack({}),
            "parent_config": None,
            "thread_ts": "2024-01-01T00:00:00+00:00",
            "checkpoint_id": "t1:2024-01-01T00:00:00+00:00",
        },
        {
            "checkpoint_data": saver._serde_pack(
                {
                    "v": 1,
                    "channel_values": {},
                    "channel_versions": {},
                    "versions_seen": {},
                    "pending_sends": [],
                    "id": "cp2",
                }
            ),
            "metadata": saver._serde_pack({}),
            "parent_config": None,
            "thread_ts": "2024-01-01T00:00:01+00:00",
            "checkpoint_id": "t1:2024-01-01T00:00:01+00:00",
        },
    ]
    writes_rows_cp1 = [
        {
            "task_id": "task1",
            "channel": "messages",
            "value": saver._serde_pack("hello"),
        },
    ]
    writes_rows_cp2 = []
    # First call: checkpoint rows; subsequent calls: pending writes per checkpoint
    mock_db.execute_query.side_effect = [
        checkpoint_rows,
        writes_rows_cp1,
        writes_rows_cp2,
    ]

    saver = HanaCheckpointSaver(db=mock_db)
    config = {"configurable": {"thread_id": "t1"}}
    results = list(saver.list(config))

    assert len(results) == 2
    # First checkpoint has a pending write
    assert results[0].pending_writes is not None
    assert len(results[0].pending_writes) == 1
    assert results[0].pending_writes[0][1] == "messages"
    # Second checkpoint has no pending writes but is not None
    assert results[1].pending_writes is not None
    assert len(results[1].pending_writes) == 0


def test_list_pending_writes_deserializes_value():
    """list must deserialize JSON-encoded value from pending writes."""
    mock_db = MagicMock()
    saver = HanaCheckpointSaver(db=mock_db)
    checkpoint_rows = [
        {
            "checkpoint_data": saver._serde_pack(
                {
                    "v": 1,
                    "channel_values": {},
                    "channel_versions": {},
                    "versions_seen": {},
                    "pending_sends": [],
                    "id": "cp1",
                }
            ),
            "metadata": saver._serde_pack({}),
            "parent_config": None,
            "thread_ts": "2024-01-01T00:00:00+00:00",
            "checkpoint_id": "t1:2024-01-01T00:00:00+00:00",
        }
    ]
    writes_rows = [
        {
            "task_id": "task1",
            "channel": "output",
            "value": saver._serde_pack({"key": "val"}),
        },
    ]
    mock_db.execute_query.side_effect = [checkpoint_rows, writes_rows]
    config = {"configurable": {"thread_id": "t1"}}
    results = list(saver.list(config))

    assert len(results) == 1
    assert results[0].pending_writes[0][2] == {"key": "val"}

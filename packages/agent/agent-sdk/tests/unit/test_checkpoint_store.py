"""Tests for HanaCheckpointSaver and create_checkpointer factory.

Covers:
- HanaCheckpointSaver is instance of BaseCheckpointSaver.
- aget_tuple returns a CheckpointTuple via async executor.
- alist yields CheckpointTuple objects via async generator.
- setup() creates tables and handles 'table already exists' (errorcode=288) gracefully.
- create_checkpointer returns HanaCheckpointSaver with setup() called in non-mock mode.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple

from agent_sdk.layer4_frameworks.persistence.checkpoint_store import (
    HanaCheckpointSaver,
    create_checkpointer,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    db = MagicMock()
    db._create_connection.return_value = MagicMock()
    return db


@pytest.fixture
def saver(mock_db):
    return HanaCheckpointSaver(db=mock_db)


@pytest.fixture
def config():
    return {"configurable": {"thread_id": "thread-1"}}


def _make_checkpoint_tuple(config):
    return CheckpointTuple(
        config=config,
        checkpoint={
            "v": 1,
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
            "id": "ckpt-1",
        },
        metadata={"source": "loop", "step": 1, "writes": None, "parents": {}},
        parent_config=None,
        pending_writes=None,
    )


# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------


def test_hana_checkpoint_saver_is_base_checkpoint_saver(saver):
    """HanaCheckpointSaver must inherit from BaseCheckpointSaver."""
    assert isinstance(saver, BaseCheckpointSaver)


# ---------------------------------------------------------------------------
# aget_tuple
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aget_tuple_returns_checkpoint_tuple(saver, config):
    """aget_tuple must return a CheckpointTuple using run_in_executor."""
    ct = _make_checkpoint_tuple(config)
    mock_loop = MagicMock()
    mock_loop.run_in_executor = AsyncMock(return_value=ct)

    with patch(
        "agent_sdk.layer4_frameworks.persistence.checkpoint_store.asyncio.get_running_loop",
        return_value=mock_loop,
    ):
        result = await saver.aget_tuple(config)

    assert isinstance(result, CheckpointTuple)
    assert result.config == config


def test_checkpoint_store_allows_root_parentless_reload(saver, mock_db, config):
    checkpoint_tuple = _make_checkpoint_tuple(config)
    mock_db.execute_query.side_effect = [
        [
            {
                "checkpoint_id": "ckpt-root",
                "checkpoint_data": saver._serde_pack(checkpoint_tuple.checkpoint),
                "metadata": saver._serde_pack(checkpoint_tuple.metadata),
                "parent_config": "null",
                "thread_ts": "2026-05-29T00:00:00+00:00",
            }
        ],
        [],
    ]

    result = saver.get_tuple(config)

    assert result is not None
    assert result.parent_config is None


def test_checkpoint_store_put_requires_native_checkpoint_timestamp(
    saver, mock_db, config
):
    checkpoint = _make_checkpoint_tuple(config).checkpoint

    with pytest.raises(ValueError, match="missing required 'ts'"):
        saver.put(config, checkpoint, {"source": "loop"}, {})

    mock_db.execute_write.assert_not_called()


# ---------------------------------------------------------------------------
# alist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alist_yields_checkpoint_tuples(saver, config):
    """alist must yield CheckpointTuple objects from run_in_executor."""
    ct = _make_checkpoint_tuple(config)
    mock_loop = MagicMock()
    mock_loop.run_in_executor = AsyncMock(return_value=[ct])

    with patch(
        "agent_sdk.layer4_frameworks.persistence.checkpoint_store.asyncio.get_running_loop",
        return_value=mock_loop,
    ):
        items = [item async for item in saver.alist(config)]

    assert len(items) == 1
    assert isinstance(items[0], CheckpointTuple)


# ---------------------------------------------------------------------------
# setup() – table creation
# ---------------------------------------------------------------------------


def test_setup_creates_tables(saver, mock_db):
    """setup() must execute both CREATE TABLE DDLs and commit; 288 errors are silently ignored."""
    import hdbcli.dbapi as hdbcli_dbapi

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    mock_db._create_connection.return_value = conn

    error_288 = hdbcli_dbapi.Error("Table already exists")
    error_288.errorcode = 288
    cursor.execute.side_effect = [error_288, None]

    saver.setup()  # must not raise

    assert cursor.execute.call_count == 2
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


def test_setup_creates_tables_happy_path(saver, mock_db):
    """setup() must execute both CREATE TABLE DDLs and commit when no errors occur."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    mock_db._create_connection.return_value = conn

    cursor.execute.return_value = None

    saver.setup()

    assert cursor.execute.call_count == 2
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# create_checkpointer
# ---------------------------------------------------------------------------


def test_create_checkpointer_returns_hana_saver():
    """create_checkpointer must return HanaCheckpointSaver and call setup() in non-mock mode."""
    mock_settings = MagicMock()
    mock_settings.INFRA_MODE = "production"
    mock_settings.CHECKPOINT_TTL_HOURS = 24

    mock_db = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    mock_db._create_connection.return_value = conn

    with patch.object(HanaCheckpointSaver, "setup") as mock_setup:
        result = create_checkpointer(mock_settings, hana_connection_manager=mock_db)

    assert isinstance(result, HanaCheckpointSaver)
    mock_setup.assert_called_once()


# ---------------------------------------------------------------------------
# cleanup_expired – return value
# ---------------------------------------------------------------------------


def test_cleanup_expired_returns_sum_of_deleted_rows(saver, mock_db):
    """cleanup_expired must return the sum of rows deleted from both tables."""
    mock_db.execute_write.side_effect = [5, 3]

    result = saver.cleanup_expired()

    assert result == 8
    assert mock_db.execute_write.call_count == 2


def test_cleanup_expired_returns_zero_when_nothing_deleted(saver, mock_db):
    """cleanup_expired must return 0 when no rows are deleted."""
    mock_db.execute_write.side_effect = [0, 0]

    result = saver.cleanup_expired()

    assert result == 0

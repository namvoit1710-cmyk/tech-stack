"""
Checkpoint store factory for LangGraph conversation flow checkpointing.

Returns:
- HanaCheckpointSaver for production (when HANA is configured)
- InMemorySaver for mock/development mode

LangGraph does not have a native SAP HANA saver, so HanaCheckpointSaver
implements the BaseCheckpointSaver interface using the existing
HanaConnectionManager.
"""

import asyncio
import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterator, Optional, Sequence, cast

import hdbcli.dbapi
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

logger = logging.getLogger(__name__)

_CREATE_CHECKPOINTS_DDL = (
    'CREATE COLUMN TABLE "AIW_FLOW_CHECKPOINTS" ('
    '"CHECKPOINT_ID" NVARCHAR(255) PRIMARY KEY, '
    '"THREAD_ID" NVARCHAR(255), '
    '"THREAD_TS" NVARCHAR(50), '
    '"CHECKPOINT_DATA" NCLOB, '
    '"METADATA" NCLOB, '
    '"PARENT_CONFIG" NCLOB, '
    '"CREATED_AT" NVARCHAR(50))'
)

_CREATE_CHECKPOINT_WRITES_DDL = (
    'CREATE COLUMN TABLE "AIW_FLOW_CHECKPOINT_WRITES" ('
    '"WRITE_ID" NVARCHAR(255) PRIMARY KEY, '
    '"THREAD_ID" NVARCHAR(255), '
    '"CHECKPOINT_ID" NVARCHAR(255), '
    '"TASK_ID" NVARCHAR(255), '
    '"CHANNEL" NVARCHAR(255), '
    '"VALUE" NCLOB, '
    '"CREATED_AT" NVARCHAR(50))'
)


class HanaCheckpointSaver(BaseCheckpointSaver):
    """LangGraph-compatible checkpoint saver backed by SAP HANA.

    Stores checkpoint data in AIW_FLOW_CHECKPOINTS and
    pending writes in AIW_FLOW_CHECKPOINT_WRITES.

    Implements the minimal interface required by LangGraph's
    compiled graph: get_tuple(), list(), put(), put_writes().

    Serialization note
    ------------------
    LangGraph's serde.dumps_typed() returns (type_str, bytes). Those bytes
    are typically MessagePack — arbitrary binary that is NOT valid UTF-8.
    Storing them via .decode('utf-8') silently corrupts data or raises
    UnicodeDecodeError at write time. Instead, we use a safe JSON envelope:

        {"t": "<type_str>", "d": "<base64-encoded bytes>"}

    This is always valid ASCII, safe for NCLOB columns, and round-trips
    perfectly regardless of the serde backend.
    """

    def __init__(self, db: Any, ttl_hours: int = 24, log: logging.Logger | None = None):
        super().__init__()
        self._db = db
        self._ttl_hours = ttl_hours
        self._logger = log or logger

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def _serde_pack(self, obj: Any) -> str:
        """Serialize *obj* to a Base64-JSON envelope safe for NCLOB storage.

        serde.dumps_typed() → (type_str, bytes).  The bytes may be binary
        (e.g. MessagePack); encoding them as UTF-8 is unsafe.  Base64 is
        always ASCII and round-trips cleanly.

        Returns a JSON string: {"t": type_str, "d": "<base64>"}
        """
        type_str, data_bytes = self.serde.dumps_typed(obj)
        return json.dumps(
            {"t": type_str, "d": base64.b64encode(data_bytes).decode("ascii")}
        )

    def _serde_unpack(self, raw: str | bytes | None) -> Any:
        """Deserialize a value produced by _serde_pack.

        Accepts the NCLOB column value (str or bytes from hdbcli) and
        returns the original Python object.  Returns None if raw is falsy.
        """
        if not raw:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        envelope = json.loads(raw)
        if envelope is None:
            return None
        type_str = envelope["t"]
        data_bytes = base64.b64decode(envelope["d"])
        # serde.loads_typed expects (type_str, bytes), not a bare bytes arg.
        return self.serde.loads_typed((type_str, data_bytes))

    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Create the checkpoint tables if they do not exist."""
        conn = self._db._create_connection()
        try:
            cursor = conn.cursor()
            for ddl in (_CREATE_CHECKPOINTS_DDL, _CREATE_CHECKPOINT_WRITES_DDL):
                try:
                    cursor.execute(ddl)
                except Exception as exc:
                    if isinstance(exc, hdbcli.dbapi.Error):
                        if getattr(exc, "errorcode", None) == 288:
                            continue
                    raise
            conn.commit()
        finally:
            conn.close()

    def _load_pending_writes(self, thread_id: str, checkpoint_id: str) -> list:
        rows = self._db.execute_query(
            'SELECT "TASK_ID", "CHANNEL", "VALUE" '
            'FROM "AIW_FLOW_CHECKPOINT_WRITES" '
            'WHERE "THREAD_ID" = :p0 AND "CHECKPOINT_ID" = :p1',
            (thread_id, checkpoint_id),
        )
        writes = []
        for row in rows:
            try:
                value = self._serde_unpack(row["value"])
            except Exception:
                # Fall back to the raw column value if the envelope is malformed
                # (e.g. rows written by an older version of this code).
                value = row["value"]
            writes.append((row["task_id"], row["channel"], value))
        return writes

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """Get the latest checkpoint for a thread.

        Args:
            config: Dict with configurable.thread_id.

        Returns:
            CheckpointTuple or None.
        """
        thread_id = config.get("configurable", {}).get("thread_id", "")
        if not thread_id:
            return None

        rows = self._db.execute_query(
            'SELECT "CHECKPOINT_ID", "CHECKPOINT_DATA", "METADATA", "PARENT_CONFIG", "THREAD_TS" '
            'FROM "AIW_FLOW_CHECKPOINTS" '
            'WHERE "THREAD_ID" = :p0 '
            'ORDER BY "THREAD_TS" DESC '
            "LIMIT 1",
            (thread_id,),
        )

        if not rows:
            return None

        row = rows[0]
        checkpoint_id = row.get("checkpoint_id", "")
        checkpoint = cast(
            Checkpoint, self._serde_unpack(row.get("checkpoint_data")) or {}
        )
        metadata = cast(
            CheckpointMetadata, self._serde_unpack(row.get("metadata")) or {}
        )
        parent_config = self._serde_unpack(row.get("parent_config"))
        pending_writes = self._load_pending_writes(thread_id, checkpoint_id)

        returned_config = cast(
            RunnableConfig,
            {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_id": checkpoint_id,
                    "thread_ts": row.get("thread_ts", ""),
                }
            },
        )

        return CheckpointTuple(
            config=returned_config,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    def list(
        self,
        config: Optional[RunnableConfig] = None,
        *,
        filter: Optional[dict] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        """List checkpoints for a thread."""
        thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
        if not thread_id:
            return iter([])

        effective_limit = limit if limit is not None else 2147483647

        query_parts = [
            'SELECT "CHECKPOINT_ID", "CHECKPOINT_DATA", "METADATA", "PARENT_CONFIG", "THREAD_TS" '
            'FROM "AIW_FLOW_CHECKPOINTS" '
            'WHERE "THREAD_ID" = :p0'
        ]
        params: list = [thread_id]

        if before:
            before_ts = before.get("configurable", {}).get("thread_ts")
            if before_ts:
                query_parts.append(f'AND "THREAD_TS" < :p{len(params)}')
                params.append(before_ts)

        query_parts.append(f'ORDER BY "THREAD_TS" DESC LIMIT :p{len(params)}')
        params.append(effective_limit)

        rows = self._db.execute_query(" ".join(query_parts), tuple(params))

        def _generate():
            for row in rows:
                checkpoint_id = row.get("checkpoint_id", "")
                checkpoint = cast(
                    Checkpoint, self._serde_unpack(row.get("checkpoint_data")) or {}
                )
                metadata = cast(
                    CheckpointMetadata, self._serde_unpack(row.get("metadata")) or {}
                )
                parent_config = self._serde_unpack(row.get("parent_config"))
                if filter:
                    if not all(metadata.get(k) == v for k, v in filter.items()):
                        continue
                pending_writes = self._load_pending_writes(thread_id, checkpoint_id)
                yield CheckpointTuple(
                    config=cast(
                        RunnableConfig,
                        {
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_id": row.get("checkpoint_id", ""),
                                "thread_ts": row.get("thread_ts", ""),
                            }
                        },
                    ),
                    checkpoint=checkpoint,
                    metadata=metadata,
                    parent_config=parent_config,
                    pending_writes=pending_writes,
                )

        return _generate()

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Save a checkpoint."""
        thread_id = config.get("configurable", {}).get("thread_id", "")

        # 🐛 FIX: Always respect LangGraph's native checkpoint ID and timestamp
        checkpoint_id = checkpoint["id"]
        thread_ts = checkpoint.get("ts")
        if not thread_ts:
            raise ValueError("Checkpoint is missing required 'ts' timestamp")

        parent_checkpoint_id = config.get("configurable", {}).get("checkpoint_id", "")

        parent_config_packed = (
            self._serde_pack({"configurable": {"checkpoint_id": parent_checkpoint_id}})
            if parent_checkpoint_id
            else None
        )

        self._db.execute_write(
            'MERGE INTO "AIW_FLOW_CHECKPOINTS" AS target '
            'USING (SELECT :p0 AS "CHECKPOINT_ID", :p1 AS "THREAD_ID", '
            ':p2 AS "THREAD_TS", :p3 AS "CHECKPOINT_DATA", '
            ':p4 AS "METADATA", :p5 AS "PARENT_CONFIG", '
            ':p6 AS "CREATED_AT" FROM DUMMY) AS src '
            'ON target."CHECKPOINT_ID" = src."CHECKPOINT_ID" '
            "WHEN MATCHED THEN UPDATE SET "
            '"CHECKPOINT_DATA" = src."CHECKPOINT_DATA", '
            '"METADATA" = src."METADATA", '
            '"THREAD_TS" = src."THREAD_TS" '
            "WHEN NOT MATCHED THEN INSERT "
            '("CHECKPOINT_ID", "THREAD_ID", "THREAD_TS", '
            '"CHECKPOINT_DATA", "METADATA", "PARENT_CONFIG", "CREATED_AT") '
            'VALUES (src."CHECKPOINT_ID", src."THREAD_ID", src."THREAD_TS", '
            'src."CHECKPOINT_DATA", src."METADATA", src."PARENT_CONFIG", '
            'src."CREATED_AT")',
            (
                checkpoint_id,
                thread_id,
                thread_ts,
                self._serde_pack(checkpoint),
                self._serde_pack(metadata),
                parent_config_packed,
                thread_ts,
            ),
        )

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
            },
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Save pending writes for a checkpoint."""
        thread_id = config.get("configurable", {}).get("thread_id", "")
        checkpoint_id = config.get("configurable", {}).get("checkpoint_id", "")
        created_at = datetime.now(timezone.utc).isoformat()

        for channel, value in writes:
            write_id = f"{checkpoint_id}:{task_id}:{channel}"
            # TODO: Batch into a single execute_many call when HanaDB supports it
            # (currently HanaDB only has execute_query and execute_write, no batch method)
            self._db.execute_write(
                'MERGE INTO "AIW_FLOW_CHECKPOINT_WRITES" AS target '
                'USING (SELECT :p0 AS "WRITE_ID", :p1 AS "THREAD_ID", '
                ':p2 AS "CHECKPOINT_ID", :p3 AS "TASK_ID", '
                ':p4 AS "CHANNEL", :p5 AS "VALUE", '
                ':p6 AS "CREATED_AT" FROM DUMMY) AS src '
                'ON target."WRITE_ID" = src."WRITE_ID" '
                "WHEN MATCHED THEN UPDATE SET "
                '"VALUE" = src."VALUE" '
                "WHEN NOT MATCHED THEN INSERT "
                '("WRITE_ID", "THREAD_ID", "CHECKPOINT_ID", "TASK_ID", '
                '"CHANNEL", "VALUE", "CREATED_AT") '
                'VALUES (src."WRITE_ID", src."THREAD_ID", src."CHECKPOINT_ID", '
                'src."TASK_ID", src."CHANNEL", src."VALUE", src."CREATED_AT")',
                (
                    write_id,
                    thread_id,
                    checkpoint_id,
                    task_id,
                    channel,
                    self._serde_pack(value),
                    created_at,
                ),
            )

    def cleanup_expired(self) -> int:
        """Delete checkpoints older than TTL. Returns count deleted."""
        from datetime import timedelta

        cutoff_ts = (
            datetime.now(timezone.utc) - timedelta(hours=self._ttl_hours)
        ).isoformat()

        try:
            writes_deleted = self._db.execute_write(
                'DELETE FROM "AIW_FLOW_CHECKPOINT_WRITES" '
                'WHERE "CHECKPOINT_ID" IN ('
                'SELECT "CHECKPOINT_ID" FROM "AIW_FLOW_CHECKPOINTS" '
                'WHERE "CREATED_AT" < :p0)',
                (cutoff_ts,),
            )
            checkpoints_deleted = self._db.execute_write(
                'DELETE FROM "AIW_FLOW_CHECKPOINTS" WHERE "CREATED_AT" < :p0',
                (cutoff_ts,),
            )
            return (writes_deleted or 0) + (checkpoints_deleted or 0)
        except Exception as exc:
            self._logger.warning("Checkpoint cleanup failed: %s", exc)
            return 0

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """Async version of get_tuple."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_tuple, config)

    async def alist(
        self,
        config: Optional[RunnableConfig] = None,
        *,
        filter: Optional[dict] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """Async generator version of list."""
        loop = asyncio.get_running_loop()
        items = await loop.run_in_executor(
            None,
            lambda: list(self.list(config, filter=filter, before=before, limit=limit)),
        )
        for item in items:
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Async version of put."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.put, config, checkpoint, metadata, new_versions
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Async version of put_writes."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self.put_writes, config, writes, task_id, task_path
        )


def create_checkpointer(
    settings: Any, hana_connection_manager: Any | None = None
) -> BaseCheckpointSaver:
    """Create a LangGraph checkpointer based on infrastructure mode.

    Args:
        settings: Application settings with INFRA_MODE.
        hana_connection_manager: Optional HanaConnectionManager for HANA-backed storage.

    Returns:
        A LangGraph-compatible checkpointer (MemorySaver or HanaCheckpointSaver).
    """
    # Persistence mode uses INFRA_MODE only (not MESSAGING_MODE which is for messaging)
    infra_mode = getattr(settings, "INFRA_MODE", "mock")
    ttl_hours = getattr(settings, "CHECKPOINT_TTL_HOURS", 24)

    if infra_mode != "mock" and hana_connection_manager:
        checkpointer = HanaCheckpointSaver(
            db=hana_connection_manager,
            ttl_hours=ttl_hours,
        )
        checkpointer.setup()
        logger.info("Checkpoint store: HanaCheckpointSaver (SAP HANA)")
        return checkpointer

    from langgraph.checkpoint.memory import MemorySaver

    checkpointer = MemorySaver()
    logger.info("Checkpoint store: MemorySaver (in-memory)")
    return checkpointer

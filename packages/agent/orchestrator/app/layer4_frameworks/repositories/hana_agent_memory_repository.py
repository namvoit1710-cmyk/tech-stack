"""HANA-backed long-term memory repository (SA-1436).

Storage lives here; policy lives in ``AgentMemoryService``. v1 recall is conservative
keyword SQL (tenant- and status-scoped LIKE) — vector recall is a later phase. Every
query is tenant-isolated (no ``TENANT_BLEED``) and active-only (deleted/archived memory
is never recalled).
"""
from __future__ import annotations

import uuid
from typing import Any

from agent_sdk.layer4_frameworks.persistence.hana.base_repository import (
    BaseHanaRepository,
    IDatabaseConnection,
)
from app.layer2_application.interfaces.agent_memory_repository import (
    IAgentMemoryRepository,
)
from app.layer1_domain.entities.agent_memory import (
    AgentMemory,
    AgentMemoryScope,
    AgentMemoryStatus,
    AgentMemoryType,
    MemoryMatch,
    MemoryQuery,
)

_MEMORY_TABLE = "AIW_AGENT_MEMORY"
_EVENT_TABLE = "AIW_AGENT_MEMORY_EVENT"

_SELECT_COLUMNS = (
    '"MEMORY_ID", "TENANT_ID", "USER_ID", "AGENT_ID", "WORKFLOW_ID", "CONV_ID", '
    '"SCOPE", "MEMORY_TYPE", "MEMORY_KEY", "CONTENT", "SUMMARY", '
    '"SOURCE_CONV_ID", "SOURCE_EVENT_ID", "SOURCE_TASK_ID", '
    '"CONFIDENCE", "IMPORTANCE", "SENSITIVITY", "STATUS", "ACCESS_COUNT", "METADATA_JSON"'
)


class HanaAgentMemoryRepository(BaseHanaRepository, IAgentMemoryRepository):
    def __init__(self, db: IDatabaseConnection) -> None:
        super().__init__(db, _MEMORY_TABLE)

    # ------------------------------------------------------------------ save
    def save(self, memory: AgentMemory) -> AgentMemory:
        sql = (
            f'INSERT INTO "{_MEMORY_TABLE}" '
            '("MEMORY_ID", "TENANT_ID", "USER_ID", "AGENT_ID", "WORKFLOW_ID", "CONV_ID", '
            '"SCOPE", "MEMORY_TYPE", "MEMORY_KEY", "CONTENT", "SUMMARY", '
            '"SOURCE_CONV_ID", "SOURCE_EVENT_ID", "SOURCE_TASK_ID", '
            '"CONFIDENCE", "IMPORTANCE", "SENSITIVITY", "STATUS", "METADATA_JSON") '
            "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9, :p10, "
            ":p11, :p12, :p13, :p14, :p15, :p16, :p17, :p18)"
        )
        params = (
            memory.memory_id,
            memory.tenant_id,
            memory.user_id,
            memory.agent_id,
            memory.workflow_id,
            memory.conv_id,
            memory.scope.value,
            memory.memory_type.value,
            memory.memory_key,
            memory.content,
            memory.summary or memory.content[:1000],
            memory.source_conv_id,
            memory.source_event_id,
            memory.source_task_id,
            float(memory.confidence),
            int(memory.importance),
            memory.sensitivity,
            memory.status.value,
            self._to_json(memory.metadata),
        )
        self._execute_write(sql, params)
        return memory

    # ---------------------------------------------------------------- search
    def search(self, query: MemoryQuery) -> list[MemoryMatch]:
        """Recall the top-K active memories for a (tenant, user/agent/workflow) scope.

        Keyword overlap with the query is a *ranking boost*, NOT a hard filter — otherwise a
        stored preference like "prefers minimal patches" would never surface for a query like
        "fix this code". Candidates are fetched by importance/recency, then re-ranked in Python
        by the hybrid score (keyword + importance + confidence). Still strictly tenant-isolated
        and active-only. (Semantic/vector recall is a later phase.)
        """
        conditions = ['"TENANT_ID" = :p0', '"STATUS" = :p1']
        params: list[Any] = [query.tenant_id, AgentMemoryStatus.ACTIVE.value]
        idx = 2

        for column, value in (
            ('"USER_ID"', query.user_id),
            ('"AGENT_ID"', query.agent_id),
            ('"WORKFLOW_ID"', query.workflow_id),
        ):
            if value:
                conditions.append(f"{column} = :p{idx}")
                params.append(value)
                idx += 1

        if query.memory_types:
            placeholders = ", ".join(f":p{idx + i}" for i in range(len(query.memory_types)))
            conditions.append(f'"MEMORY_TYPE" IN ({placeholders})')
            params += [t.value for t in query.memory_types]
            idx += len(query.memory_types)

        if query.scopes:
            placeholders = ", ".join(f":p{idx + i}" for i in range(len(query.scopes)))
            conditions.append(f'"SCOPE" IN ({placeholders})')
            params += [s.value for s in query.scopes]
            idx += len(query.scopes)

        top_k = max(1, int(query.top_k or 5))
        # Candidate window: pull more than top_k by importance/recency, then re-rank by score.
        window = max(top_k * 4, 20)
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM \"{_MEMORY_TABLE}\" "
            f"WHERE {' AND '.join(conditions)} "
            'ORDER BY "IMPORTANCE" DESC, "CONFIDENCE" DESC, "UPDATED_AT" DESC '
            f"LIMIT {window}"
        )
        rows = self._execute_query(sql, tuple(params))
        matches = [self._row_to_match(row, query.query or "") for row in rows]
        matches.sort(key=lambda m: m.score, reverse=True)
        return matches[:top_k]

    # ---------------------------------------------------------- find_similar
    def find_similar(
        self,
        *,
        tenant_id: str,
        user_id: str,
        content: str,
        memory_type: str,
        limit: int = 5,
    ) -> list[AgentMemory]:
        conditions = ['"TENANT_ID" = :p0', '"STATUS" = :p1', '"MEMORY_TYPE" = :p2']
        params: list[Any] = [tenant_id, AgentMemoryStatus.ACTIVE.value, memory_type]
        idx = 3
        if user_id:
            conditions.append(f'"USER_ID" = :p{idx}')
            params.append(user_id)
            idx += 1
        conditions.append(f'LOWER("CONTENT") LIKE :p{idx}')
        params.append(f"%{(content or '').lower()[:200]}%")

        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM \"{_MEMORY_TABLE}\" "
            f"WHERE {' AND '.join(conditions)} "
            f"LIMIT {max(1, int(limit))}"
        )
        rows = self._execute_query(sql, tuple(params))
        return [self._row_to_memory(row) for row in rows]

    # --------------------------------------------------------- mark_accessed
    def mark_accessed(self, memory_ids: list[str], *, tenant_id: str = "") -> None:
        cleaned = [m for m in (memory_ids or []) if isinstance(m, str) and m]
        if not cleaned:
            return
        placeholders = ", ".join(f":p{i}" for i in range(len(cleaned)))
        sql = (
            f'UPDATE "{_MEMORY_TABLE}" '
            'SET "ACCESS_COUNT" = "ACCESS_COUNT" + 1, "LAST_ACCESSED_AT" = CURRENT_TIMESTAMP '
            f'WHERE "MEMORY_ID" IN ({placeholders})'
        )
        params: list[Any] = list(cleaned)
        # SA-1436 S-1: scope the UPDATE to the tenant too (defense-in-depth — a leaked
        # id can never cross tenants), when the caller resolved a tenant.
        if tenant_id:
            sql += f' AND "TENANT_ID" = :p{len(cleaned)}'
            params.append(tenant_id)
        self._execute_write(sql, tuple(params))

    # ---------------------------------------------------------------- forget
    def forget(self, memory_id: str, *, reason: str = "", tenant_id: str = "") -> None:
        sql = (
            f'UPDATE "{_MEMORY_TABLE}" '
            f"SET \"STATUS\" = '{AgentMemoryStatus.DELETED.value}', \"UPDATED_AT\" = CURRENT_TIMESTAMP "
            'WHERE "MEMORY_ID" = :p0'
        )
        params: list[Any] = [memory_id]
        # SA-1436 S-1: tenant-scope the soft-delete as defense-in-depth.
        if tenant_id:
            sql += ' AND "TENANT_ID" = :p1'
            params.append(tenant_id)
        self._execute_write(sql, tuple(params))

    # ------------------------------------------------------------ save_event
    def save_event(
        self,
        *,
        memory_id: str,
        tenant_id: str,
        user_id: str,
        event_type: str,
        reason: str = "",
        payload: dict | None = None,
    ) -> None:
        sql = (
            f'INSERT INTO "{_EVENT_TABLE}" '
            '("EVENT_ID", "MEMORY_ID", "TENANT_ID", "USER_ID", "EVENT_TYPE", "REASON", "PAYLOAD_JSON") '
            "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6)"
        )
        params = (
            uuid.uuid4().hex,
            memory_id,
            tenant_id,
            user_id,
            event_type,
            reason,
            self._to_json(payload or {}),
        )
        self._execute_write(sql, params)

    # ----------------------------------------------------------- row mappers
    @staticmethod
    def _get(row: dict[str, Any], key: str, default: Any = "") -> Any:
        if key in row:
            return row[key]
        upper = row.get(key.upper())
        if upper is not None:
            return upper
        lower = row.get(key.lower())
        return lower if lower is not None else default

    def _row_to_memory(self, row: dict[str, Any]) -> AgentMemory:
        try:
            confidence = float(self._get(row, "CONFIDENCE", 0.8) or 0.8)
        except (TypeError, ValueError):
            confidence = 0.8
        try:
            importance = int(self._get(row, "IMPORTANCE", 3) or 3)
        except (TypeError, ValueError):
            importance = 3
        try:
            access_count = int(self._get(row, "ACCESS_COUNT", 0) or 0)
        except (TypeError, ValueError):
            access_count = 0

        metadata = self._from_json(self._get(row, "METADATA_JSON", None))
        if not isinstance(metadata, dict):
            metadata = {}

        return AgentMemory(
            memory_id=str(self._get(row, "MEMORY_ID", "")),
            tenant_id=str(self._get(row, "TENANT_ID", "")),
            scope=AgentMemoryScope.coerce(self._get(row, "SCOPE", "")),
            memory_type=AgentMemoryType.coerce(self._get(row, "MEMORY_TYPE", "")),
            content=str(self._get(row, "CONTENT", "")),
            user_id=str(self._get(row, "USER_ID", "") or ""),
            agent_id=str(self._get(row, "AGENT_ID", "") or ""),
            workflow_id=str(self._get(row, "WORKFLOW_ID", "") or ""),
            conv_id=str(self._get(row, "CONV_ID", "") or ""),
            memory_key=str(self._get(row, "MEMORY_KEY", "") or ""),
            summary=str(self._get(row, "SUMMARY", "") or ""),
            source_conv_id=str(self._get(row, "SOURCE_CONV_ID", "") or ""),
            source_event_id=str(self._get(row, "SOURCE_EVENT_ID", "") or ""),
            source_task_id=str(self._get(row, "SOURCE_TASK_ID", "") or ""),
            confidence=confidence,
            importance=importance,
            sensitivity=str(self._get(row, "SENSITIVITY", "normal") or "normal"),
            status=AgentMemoryStatus.coerce(self._get(row, "STATUS", "active")),
            access_count=access_count,
            metadata=metadata,
        )

    def _row_to_match(self, row: dict[str, Any], query_text: str) -> MemoryMatch:
        memory = self._row_to_memory(row)
        return MemoryMatch(
            memory=memory,
            score=self._score(memory, query_text),
            reason="keyword+importance recall (v1)",
        )

    @staticmethod
    def _score(memory: AgentMemory, query_text: str) -> float:
        """Transparent v1 score: confidence + importance + keyword overlap.

        Replaced by hybrid vector ranking in a later phase.
        """
        tokens = [t for t in (query_text or "").lower().split() if len(t) > 2]
        haystack = f"{memory.content} {memory.summary} {memory.memory_key}".lower()
        keyword = 0.0
        if tokens:
            hits = sum(1 for t in set(tokens) if t in haystack)
            keyword = hits / len(set(tokens))
        score = 0.5 * memory.confidence + 0.3 * (memory.importance / 5.0) + 0.2 * keyword
        return round(min(1.0, score), 4)

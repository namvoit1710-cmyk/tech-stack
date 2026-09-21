"""Repository for orchestrator-owned conversation titles."""
from __future__ import annotations

from typing import Any

from app.layer2_application.interfaces.conversation_summary_repository import (
    ConversationTitleSource,
    ConversationTitleSummary,
    IConversationSummaryRepository,
)
from agent_sdk.layer4_frameworks.persistence.hana.base_repository import BaseHanaRepository, IDatabaseConnection


class HanaConversationSummaryRepository(BaseHanaRepository, IConversationSummaryRepository):
    def __init__(self, db: IDatabaseConnection):
        super().__init__(db, "AIW_CONVERSATION_SUMMARY")

    def save_title(self, summary: ConversationTitleSummary) -> None:
        # SA-2168: a preserved title (strategy or a user_rename) is never overwritten
        # by a later, lower-precedence auto-title write (strategy/user_message).
        sql = (
            "MERGE INTO AIW_CONVERSATION_SUMMARY AS target "
            "USING (SELECT :p0 AS CONV_ID, :p1 AS TITLE, :p2 AS TITLE_SOURCE FROM DUMMY) AS src "
            "ON target.CONV_ID = src.CONV_ID "
            "WHEN MATCHED THEN UPDATE SET "
            "target.TITLE = CASE "
            "WHEN target.TITLE_SOURCE IN ('strategy_context_summary', 'user_rename') AND src.TITLE_SOURCE <> target.TITLE_SOURCE THEN target.TITLE "
            "ELSE src.TITLE END, "
            "target.TITLE_SOURCE = CASE "
            "WHEN target.TITLE_SOURCE IN ('strategy_context_summary', 'user_rename') AND src.TITLE_SOURCE <> target.TITLE_SOURCE THEN target.TITLE_SOURCE "
            "ELSE src.TITLE_SOURCE END, "
            "target.UPDATED_AT = CURRENT_TIMESTAMP "
            "WHEN NOT MATCHED THEN INSERT (CONV_ID, TITLE, TITLE_SOURCE, CREATED_AT, UPDATED_AT) "
            "VALUES (src.CONV_ID, src.TITLE, src.TITLE_SOURCE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        self._execute_write(sql, (summary.conv_id, summary.title, summary.source.value))

    def update_title(self, conv_id: str, title: str) -> None:
        # SA-2168: an explicit /rename is unconditional — it always wins, regardless of
        # the existing TITLE_SOURCE (that's what makes it the highest-precedence source).
        sql = (
            "MERGE INTO AIW_CONVERSATION_SUMMARY AS target "
            "USING (SELECT :p0 AS CONV_ID, :p1 AS TITLE, :p2 AS TITLE_SOURCE FROM DUMMY) AS src "
            "ON target.CONV_ID = src.CONV_ID "
            "WHEN MATCHED THEN UPDATE SET "
            "target.TITLE = src.TITLE, "
            "target.TITLE_SOURCE = src.TITLE_SOURCE, "
            "target.UPDATED_AT = CURRENT_TIMESTAMP "
            "WHEN NOT MATCHED THEN INSERT (CONV_ID, TITLE, TITLE_SOURCE, CREATED_AT, UPDATED_AT) "
            "VALUES (src.CONV_ID, src.TITLE, src.TITLE_SOURCE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        self._execute_write(sql, (conv_id, title, ConversationTitleSource.USER_RENAME.value))

    def get_title(self, conv_id: str) -> ConversationTitleSummary | None:
        sql = (
            "SELECT CONV_ID, TITLE, TITLE_SOURCE "
            "FROM AIW_CONVERSATION_SUMMARY "
            "WHERE CONV_ID = :p0"
        )
        rows = self._execute_query(sql, (conv_id,))
        if not rows:
            return None
        return self._row_to_summary(rows[0])

    def get_titles(self, conv_ids: list[str]) -> dict[str, ConversationTitleSummary]:
        cleaned_ids = [conv_id for conv_id in conv_ids if isinstance(conv_id, str) and conv_id]
        if not cleaned_ids:
            return {}

        placeholders = ", ".join(f":p{i}" for i in range(len(cleaned_ids)))
        sql = (
            "SELECT CONV_ID, TITLE, TITLE_SOURCE "
            "FROM AIW_CONVERSATION_SUMMARY "
            f"WHERE CONV_ID IN ({placeholders})"
        )
        rows = self._execute_query(sql, tuple(cleaned_ids))
        summaries = [self._row_to_summary(row) for row in rows]
        return {summary.conv_id: summary for summary in summaries}

    @staticmethod
    def _row_to_summary(row: dict[str, Any]) -> ConversationTitleSummary:
        raw_source = row.get("title_source", row.get("TITLE_SOURCE", ""))
        source = ConversationTitleSource.USER_MESSAGE
        if isinstance(raw_source, str) and raw_source:
            try:
                source = ConversationTitleSource(raw_source)
            except ValueError:
                source = ConversationTitleSource.USER_MESSAGE
        return ConversationTitleSummary(
            conv_id=row.get("conv_id", row.get("CONV_ID", "")),
            title=row.get("title", row.get("TITLE", "")),
            source=source,
        )

"""SAP HANA implementation of IUserProfileRepository."""
from __future__ import annotations

from typing import Optional

from app.layer1_domain.entities.permission.user_profile import (
    ExpertiseLevel,
    PrivacySettings,
    UserProfile,
)
from agent_sdk.layer4_frameworks.persistence.hana.base_repository import BaseHanaRepository, IDatabaseConnection

class HanaUserProfileRepository(BaseHanaRepository):

    def __init__(self, db: IDatabaseConnection):
        super().__init__(db, "AIW_USER_PROFILES")

    def get(self, user_id: str, tenant_id: str) -> Optional[UserProfile]:
        rows = self._execute_query(
            f"SELECT * FROM {self._table} WHERE USER_ID = :p0 AND TENANT_ID = :p1",
            (user_id, tenant_id),
        )
        if not rows:
            return None
        return self._row_to_profile(rows[0])

    def save(self, profile: UserProfile) -> None:
        columns = [
            "USER_ID", "TENANT_ID", "EXPERTISE_LEVEL",
            "PREFERRED_DOMAINS", "INTERACTION_COUNT",
            "SUCCESSFUL_WORKFLOWS", "FAILED_WORKFLOWS",
            "MOST_USED_AGENTS", "PRIVACY_SETTINGS", "PREFERENCES",
            "CREATED_AT", "UPDATED_AT",
        ]
        values = (
            profile.user_id,
            profile.tenant_id,
            profile.expertise_level.value,
            self._to_json(profile.preferred_domains),
            profile.interaction_count,
            profile.successful_workflows,
            profile.failed_workflows,
            self._to_json(profile.most_used_agents),
            self._to_json(profile.privacy_settings),
            self._to_json(profile.preferences),
            profile.created_at,
            profile.updated_at,
        )
        self._upsert_composite(columns, values)

    def delete(self, user_id: str, tenant_id: str) -> None:
        self._execute_write(
            f"DELETE FROM {self._table} WHERE USER_ID = :p0 AND TENANT_ID = :p1",
            (user_id, tenant_id),
        )

    def _upsert_composite(self, columns: list[str], values: tuple) -> None:
        src_cols = ", ".join(
            f":p{i} AS {col}" for i, col in enumerate(columns)
        )
        on_clause = (
            "target.USER_ID = src.USER_ID AND target.TENANT_ID = src.TENANT_ID"
        )
        update_sets = ", ".join(
            f"target.{col} = src.{col}"
            for col in columns
            if col not in ("USER_ID", "TENANT_ID")
        )
        insert_cols = ", ".join(columns)
        insert_vals = ", ".join(f"src.{col}" for col in columns)

        sql = (
            f"MERGE INTO {self._table} AS target "
            f"USING (SELECT {src_cols} FROM DUMMY) AS src "
            f"ON {on_clause} "
            f"WHEN MATCHED THEN UPDATE SET {update_sets} "
            f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})"
        )
        self._execute_write(sql, values)

    def _row_to_profile(self, row: dict) -> UserProfile:
        expertise_str = row.get("EXPERTISE_LEVEL", "beginner")
        try:
            expertise_level = ExpertiseLevel(expertise_str)
        except ValueError:
            expertise_level = ExpertiseLevel.BEGINNER

        privacy_data = self._from_json(row.get("PRIVACY_SETTINGS"))
        if isinstance(privacy_data, dict):
            privacy_settings = PrivacySettings(
                track_journey=privacy_data.get("track_journey", True),
                track_preferences=privacy_data.get("track_preferences", True),
                track_errors=privacy_data.get("track_errors", True),
                retention_days=privacy_data.get("retention_days", 90),
                anonymize_after_days=privacy_data.get("anonymize_after_days"),
            )
        else:
            privacy_settings = PrivacySettings()

        return UserProfile(
            user_id=row["user_id"],
            tenant_id=row.get("tenant_id", ""),
            expertise_level=expertise_level,
            preferred_domains=self._from_json(row.get("preferred_domains")) or [],
            interaction_count=row.get("interaction_count", 0),
            successful_workflows=row.get("successful_workflows", 0),
            failed_workflows=row.get("failed_workflows", 0),
            most_used_agents=self._from_json(row.get("most_used_agents")) or [],
            privacy_settings=privacy_settings,
            preferences=self._from_json(row.get("preferences")) or {},
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
        )

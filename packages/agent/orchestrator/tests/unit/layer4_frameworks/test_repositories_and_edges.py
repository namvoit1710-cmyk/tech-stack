from unittest.mock import MagicMock

from app.layer1_domain.entities.permission.user_profile import ExpertiseLevel, PrivacySettings, UserProfile
from app.layer4_frameworks.repositories.user_profile_repository import HanaUserProfileRepository


def _build_profile() -> UserProfile:
	return UserProfile(
		user_id="user-1",
		tenant_id="tenant-1",
		expertise_level=ExpertiseLevel.ADVANCED,
		preferred_domains=["mdg"],
		interaction_count=7,
		successful_workflows=3,
		failed_workflows=1,
		most_used_agents=["agent-a"],
		privacy_settings=PrivacySettings(track_journey=False, retention_days=30),
		preferences={"language": "en"},
		created_at="2024-01-01T00:00:00+00:00",
		updated_at="2024-01-02T00:00:00+00:00",
	)


def test_user_profile_repository_get_returns_none_when_missing():
	db = MagicMock()
	db.execute_query.return_value = []
	repo = HanaUserProfileRepository(db)

	result = repo.get("user-1", "tenant-1")

	assert result is None


def test_user_profile_repository_save_and_delete_emit_expected_sql():
	db = MagicMock()
	repo = HanaUserProfileRepository(db)

	repo.save(_build_profile())
	save_sql, save_params = db.execute_write.call_args.args
	assert "MERGE INTO AIW_USER_PROFILES AS target" in save_sql
	assert save_params[0] == "user-1"
	assert save_params[1] == "tenant-1"

	repo.delete("user-1", "tenant-1")
	delete_sql, delete_params = db.execute_write.call_args.args
	assert delete_sql == "DELETE FROM AIW_USER_PROFILES WHERE USER_ID = :p0 AND TENANT_ID = :p1"
	assert delete_params == ("user-1", "tenant-1")


def test_user_profile_repository_maps_row_to_profile_with_defaults():
	db = MagicMock()
	repo = HanaUserProfileRepository(db)
	row = {
		"user_id": "user-1",
		"tenant_id": "tenant-1",
		"EXPERTISE_LEVEL": "unknown",
		"preferred_domains": '["finance"]',
		"interaction_count": 11,
		"successful_workflows": 4,
		"failed_workflows": 2,
		"most_used_agents": '["agent-a"]',
		"PRIVACY_SETTINGS": '{"track_journey": false, "retention_days": 15}',
		"preferences": '{"theme": "light"}',
		"created_at": "created",
		"updated_at": "updated",
	}

	profile = repo._row_to_profile(row)

	assert profile.user_id == "user-1"
	assert profile.tenant_id == "tenant-1"
	assert profile.expertise_level == ExpertiseLevel.BEGINNER
	assert profile.preferred_domains == ["finance"]
	assert profile.most_used_agents == ["agent-a"]
	assert profile.privacy_settings.track_journey is False
	assert profile.privacy_settings.track_preferences is True
	assert profile.preferences == {"theme": "light"}

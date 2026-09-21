"""HANA DDL for LaidonLLM core tables (SA-1488, AC3).

Translated from the fresh clone's ``schema.prisma``. Type mapping:
  Prisma String[] / Json  -> NCLOB (JSON-encoded by the repository layer)
  Prisma String           -> NVARCHAR(255) for ids/PKs, NVARCHAR(5000) otherwise
  Prisma Float            -> DOUBLE
  Prisma Int              -> INTEGER
  Prisma BigInt           -> BIGINT
  Prisma Boolean          -> BOOLEAN
  Prisma DateTime         -> TIMESTAMP

Identifiers are double-quoted to preserve LiteLLM's mixed-case names (and to
sidestep HANA reserved words like "user"/"response"/"status"). LOB (NCLOB)
columns carry no DEFAULT (HANA forbids defaults on LOBs) — the repository
supplies '{}' / '[]' on insert.

Leaf module: stdlib-only, standalone-testable.
"""
from __future__ import annotations

# Base tables first (referenced by others). migrations.py enforces idempotency.
TABLE_ORDER = [
    "LiteLLM_Config",
    "LiteLLM_TeamTable",
    "LiteLLM_UserTable",
    "LiteLLM_VerificationToken",
    "LiteLLM_SpendLogs",
    "LiteLLM_CredentialsTable",
    "LiteLLM_ProxyModelTable",
    "LiteLLM_BudgetTable",
    "LiteLLM_OrganizationTable",
    "LiteLLM_ObjectPermissionTable",
    "LiteLLM_EndUserTable",
    "LiteLLM_TeamMembership",
    "LiteLLM_OrganizationMembership",
    "LiteLLM_InvitationLink",
    "LiteLLM_TagTable",
    "LiteLLM_DailyUserSpend",
    "LiteLLM_DailyTeamSpend",
    "LiteLLM_DailyTagSpend",
    "LiteLLM_DailyOrganizationSpend",
    "LiteLLM_SearchToolsTable",
    "LiteLLM_DailyEndUserSpend",
    "LiteLLM_DeletedVerificationToken",
    "LiteLLM_DeletedTeamTable",
    "LiteLLM_SSOConfig",
    "LiteLLM_UISettings",
]

# Daily-spend rollup columns shared by the per-entity tables (differ only by the
# leading entity column). Kept as a helper so the four DDLs stay in lockstep.
_DAILY_SPEND_COLS = '''
        "date" NVARCHAR(255),
        "api_key" NVARCHAR(255),
        "model" NVARCHAR(5000),
        "model_group" NVARCHAR(5000),
        "custom_llm_provider" NVARCHAR(255),
        "mcp_namespaced_tool_name" NVARCHAR(5000),
        "endpoint" NVARCHAR(5000),
        "prompt_tokens" BIGINT DEFAULT 0,
        "completion_tokens" BIGINT DEFAULT 0,
        "cache_read_input_tokens" BIGINT DEFAULT 0,
        "cache_creation_input_tokens" BIGINT DEFAULT 0,
        "spend" DOUBLE DEFAULT 0,
        "api_requests" BIGINT DEFAULT 0,
        "successful_requests" BIGINT DEFAULT 0,
        "failed_requests" BIGINT DEFAULT 0,
        "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )'''

CORE_TABLE_DDL = {
    "LiteLLM_Config": '''CREATE COLUMN TABLE "LiteLLM_Config" (
        "param_name" NVARCHAR(255) PRIMARY KEY,
        "param_value" NCLOB
    )''',

    "LiteLLM_TeamTable": '''CREATE COLUMN TABLE "LiteLLM_TeamTable" (
        "team_id" NVARCHAR(255) PRIMARY KEY,
        "team_alias" NVARCHAR(5000),
        "organization_id" NVARCHAR(255),
        "object_permission_id" NVARCHAR(255),
        "admins" NCLOB,
        "members" NCLOB,
        "members_with_roles" NCLOB,
        "metadata" NCLOB,
        "max_budget" DOUBLE,
        "soft_budget" DOUBLE,
        "spend" DOUBLE DEFAULT 0,
        "models" NCLOB,
        "max_parallel_requests" INTEGER,
        "tpm_limit" BIGINT,
        "rpm_limit" BIGINT,
        "budget_duration" NVARCHAR(255),
        "budget_reset_at" TIMESTAMP,
        "blocked" BOOLEAN DEFAULT FALSE,
        "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "model_spend" NCLOB,
        "model_max_budget" NCLOB,
        "router_settings" NCLOB,
        "team_member_permissions" NCLOB,
        "access_group_ids" NCLOB,
        "policies" NCLOB,
        "default_team_member_models" NCLOB,
        "budget_limits" NCLOB,
        "model_id" INTEGER,
        "allow_team_guardrail_config" BOOLEAN DEFAULT FALSE
    )''',

    "LiteLLM_UserTable": '''CREATE COLUMN TABLE "LiteLLM_UserTable" (
        "user_id" NVARCHAR(255) PRIMARY KEY,
        "user_alias" NVARCHAR(5000),
        "team_id" NVARCHAR(255),
        "sso_user_id" NVARCHAR(255),
        "organization_id" NVARCHAR(255),
        "object_permission_id" NVARCHAR(255),
        "password" NVARCHAR(5000),
        "teams" NCLOB,
        "user_role" NVARCHAR(255),
        "max_budget" DOUBLE,
        "spend" DOUBLE DEFAULT 0,
        "user_email" NVARCHAR(5000),
        "models" NCLOB,
        "metadata" NCLOB,
        "max_parallel_requests" INTEGER,
        "tpm_limit" BIGINT,
        "rpm_limit" BIGINT,
        "budget_duration" NVARCHAR(255),
        "budget_reset_at" TIMESTAMP,
        "allowed_cache_controls" NCLOB,
        "policies" NCLOB,
        "model_spend" NCLOB,
        "model_max_budget" NCLOB,
        "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''',

    "LiteLLM_VerificationToken": '''CREATE COLUMN TABLE "LiteLLM_VerificationToken" (
        "token" NVARCHAR(255) PRIMARY KEY,
        "key_name" NVARCHAR(255),
        "key_alias" NVARCHAR(255),
        "soft_budget_cooldown" BOOLEAN DEFAULT FALSE,
        "spend" DOUBLE DEFAULT 0,
        "expires" TIMESTAMP,
        "models" NCLOB,
        "aliases" NCLOB,
        "config" NCLOB,
        "router_settings" NCLOB,
        "user_id" NVARCHAR(255),
        "team_id" NVARCHAR(255),
        "agent_id" NVARCHAR(255),
        "project_id" NVARCHAR(255),
        "permissions" NCLOB,
        "max_parallel_requests" INTEGER,
        "metadata" NCLOB,
        "blocked" BOOLEAN,
        "tpm_limit" BIGINT,
        "rpm_limit" BIGINT,
        "max_budget" DOUBLE,
        "budget_duration" NVARCHAR(255),
        "budget_reset_at" TIMESTAMP,
        "allowed_cache_controls" NCLOB,
        "allowed_routes" NCLOB,
        "policies" NCLOB,
        "access_group_ids" NCLOB,
        "model_spend" NCLOB,
        "model_max_budget" NCLOB,
        "budget_id" NVARCHAR(255),
        "organization_id" NVARCHAR(255),
        "object_permission_id" NVARCHAR(255),
        "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "created_by" NVARCHAR(255),
        "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "updated_by" NVARCHAR(255),
        "last_active" TIMESTAMP,
        "rotation_count" INTEGER DEFAULT 0,
        "auto_rotate" BOOLEAN DEFAULT FALSE,
        "rotation_interval" NVARCHAR(255),
        "last_rotation_at" TIMESTAMP,
        "key_rotation_at" TIMESTAMP,
        "budget_limits" NCLOB
    )''',

    "LiteLLM_SpendLogs": '''CREATE COLUMN TABLE "LiteLLM_SpendLogs" (
        "request_id" NVARCHAR(255) PRIMARY KEY,
        "call_type" NVARCHAR(255),
        "api_key" NVARCHAR(255) DEFAULT '',
        "spend" DOUBLE DEFAULT 0,
        "total_tokens" INTEGER DEFAULT 0,
        "prompt_tokens" INTEGER DEFAULT 0,
        "completion_tokens" INTEGER DEFAULT 0,
        "startTime" TIMESTAMP,
        "endTime" TIMESTAMP,
        "request_duration_ms" INTEGER,
        "completionStartTime" TIMESTAMP,
        "model" NVARCHAR(5000) DEFAULT '',
        "model_id" NVARCHAR(255),
        "model_group" NVARCHAR(5000),
        "custom_llm_provider" NVARCHAR(255),
        "api_base" NVARCHAR(5000),
        "user" NVARCHAR(5000),
        "metadata" NCLOB,
        "cache_hit" NVARCHAR(255),
        "cache_key" NVARCHAR(5000),
        "request_tags" NCLOB,
        "team_id" NVARCHAR(255),
        "organization_id" NVARCHAR(255),
        "end_user" NVARCHAR(5000),
        "requester_ip_address" NVARCHAR(255),
        "messages" NCLOB,
        "response" NCLOB,
        "session_id" NVARCHAR(255),
        "status" NVARCHAR(255),
        "mcp_namespaced_tool_name" NVARCHAR(5000),
        "agent_id" NVARCHAR(255),
        "proxy_server_request" NCLOB
    )''',

    "LiteLLM_CredentialsTable": '''CREATE COLUMN TABLE "LiteLLM_CredentialsTable" (
        "credential_id" NVARCHAR(255) PRIMARY KEY,
        "credential_name" NVARCHAR(255) UNIQUE,
        "credential_values" NCLOB,
        "credential_info" NCLOB,
        "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "created_by" NVARCHAR(255),
        "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "updated_by" NVARCHAR(255)
    )''',

    "LiteLLM_ProxyModelTable": '''CREATE COLUMN TABLE "LiteLLM_ProxyModelTable" (
        "model_id" NVARCHAR(255) PRIMARY KEY,
        "model_name" NVARCHAR(5000),
        "litellm_params" NCLOB,
        "model_info" NCLOB,
        "blocked" BOOLEAN DEFAULT FALSE,
        "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "created_by" NVARCHAR(255),
        "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "updated_by" NVARCHAR(255)
    )''',

    "LiteLLM_BudgetTable": '''CREATE COLUMN TABLE "LiteLLM_BudgetTable" (
        "budget_id" NVARCHAR(255) PRIMARY KEY,
        "max_budget" DOUBLE,
        "soft_budget" DOUBLE,
        "max_parallel_requests" INTEGER,
        "tpm_limit" BIGINT,
        "rpm_limit" BIGINT,
        "model_max_budget" NCLOB,
        "budget_duration" NVARCHAR(255),
        "budget_reset_at" TIMESTAMP,
        "allowed_models" NCLOB,
        "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "created_by" NVARCHAR(255),
        "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "updated_by" NVARCHAR(255)
    )''',

    "LiteLLM_OrganizationTable": '''CREATE COLUMN TABLE "LiteLLM_OrganizationTable" (
        "organization_id" NVARCHAR(255) PRIMARY KEY,
        "organization_alias" NVARCHAR(5000),
        "budget_id" NVARCHAR(255),
        "metadata" NCLOB,
        "models" NCLOB,
        "spend" DOUBLE DEFAULT 0,
        "model_spend" NCLOB,
        "object_permission_id" NVARCHAR(255),
        "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "created_by" NVARCHAR(255),
        "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "updated_by" NVARCHAR(255)
    )''',

    "LiteLLM_ObjectPermissionTable": '''CREATE COLUMN TABLE "LiteLLM_ObjectPermissionTable" (
        "object_permission_id" NVARCHAR(255) PRIMARY KEY,
        "mcp_servers" NCLOB,
        "mcp_access_groups" NCLOB,
        "mcp_tool_permissions" NCLOB,
        "vector_stores" NCLOB,
        "agents" NCLOB,
        "agent_access_groups" NCLOB,
        "models" NCLOB,
        "blocked_tools" NCLOB,
        "mcp_toolsets" NCLOB,
        "search_tools" NCLOB,
        "mcp_tool_search_enabled" BOOLEAN
    )''',

    "LiteLLM_EndUserTable": '''CREATE COLUMN TABLE "LiteLLM_EndUserTable" (
        "user_id" NVARCHAR(255) PRIMARY KEY,
        "alias" NVARCHAR(5000),
        "spend" DOUBLE DEFAULT 0,
        "allowed_model_region" NVARCHAR(255),
        "default_model" NVARCHAR(5000),
        "budget_id" NVARCHAR(255),
        "object_permission_id" NVARCHAR(255),
        "blocked" BOOLEAN DEFAULT FALSE
    )''',

    "LiteLLM_TeamMembership": '''CREATE COLUMN TABLE "LiteLLM_TeamMembership" (
        "user_id" NVARCHAR(255),
        "team_id" NVARCHAR(255),
        "spend" DOUBLE DEFAULT 0,
        "total_spend" DOUBLE DEFAULT 0,
        "budget_id" NVARCHAR(255),
        PRIMARY KEY ("user_id", "team_id")
    )''',

    "LiteLLM_OrganizationMembership": '''CREATE COLUMN TABLE "LiteLLM_OrganizationMembership" (
        "user_id" NVARCHAR(255),
        "organization_id" NVARCHAR(255),
        "user_role" NVARCHAR(255),
        "spend" DOUBLE DEFAULT 0,
        "budget_id" NVARCHAR(255),
        "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY ("user_id", "organization_id")
    )''',

    "LiteLLM_InvitationLink": '''CREATE COLUMN TABLE "LiteLLM_InvitationLink" (
        "id" NVARCHAR(255) PRIMARY KEY,
        "user_id" NVARCHAR(255),
        "is_accepted" BOOLEAN DEFAULT FALSE,
        "accepted_at" TIMESTAMP,
        "expires_at" TIMESTAMP,
        "created_at" TIMESTAMP,
        "created_by" NVARCHAR(255),
        "updated_at" TIMESTAMP,
        "updated_by" NVARCHAR(255)
    )''',

    "LiteLLM_TagTable": '''CREATE COLUMN TABLE "LiteLLM_TagTable" (
        "tag_name" NVARCHAR(255) PRIMARY KEY,
        "description" NVARCHAR(5000),
        "models" NCLOB,
        "model_info" NCLOB,
        "spend" DOUBLE DEFAULT 0,
        "budget_id" NVARCHAR(255),
        "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "created_by" NVARCHAR(255),
        "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''',

    "LiteLLM_DailyUserSpend": '''CREATE COLUMN TABLE "LiteLLM_DailyUserSpend" (
        "id" NVARCHAR(255) PRIMARY KEY,
        "user_id" NVARCHAR(255),
        "date" NVARCHAR(255),
        "api_key" NVARCHAR(255),
        "model" NVARCHAR(5000),
        "model_group" NVARCHAR(5000),
        "custom_llm_provider" NVARCHAR(255),
        "mcp_namespaced_tool_name" NVARCHAR(5000),
        "endpoint" NVARCHAR(5000),
        "prompt_tokens" BIGINT DEFAULT 0,
        "completion_tokens" BIGINT DEFAULT 0,
        "cache_read_input_tokens" BIGINT DEFAULT 0,
        "cache_creation_input_tokens" BIGINT DEFAULT 0,
        "spend" DOUBLE DEFAULT 0,
        "api_requests" BIGINT DEFAULT 0,
        "successful_requests" BIGINT DEFAULT 0,
        "failed_requests" BIGINT DEFAULT 0,
        "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''',

    "LiteLLM_DailyTeamSpend": '''CREATE COLUMN TABLE "LiteLLM_DailyTeamSpend" (
        "id" NVARCHAR(255) PRIMARY KEY,
        "team_id" NVARCHAR(255),''' + _DAILY_SPEND_COLS,

    "LiteLLM_DailyTagSpend": '''CREATE COLUMN TABLE "LiteLLM_DailyTagSpend" (
        "id" NVARCHAR(255) PRIMARY KEY,
        "request_id" NVARCHAR(255),
        "tag" NVARCHAR(5000),''' + _DAILY_SPEND_COLS,

    "LiteLLM_DailyOrganizationSpend": '''CREATE COLUMN TABLE "LiteLLM_DailyOrganizationSpend" (
        "id" NVARCHAR(255) PRIMARY KEY,
        "organization_id" NVARCHAR(255),''' + _DAILY_SPEND_COLS,

    "LiteLLM_SearchToolsTable": '''CREATE COLUMN TABLE "LiteLLM_SearchToolsTable" (
        "search_tool_id" NVARCHAR(255) PRIMARY KEY,
        "search_tool_name" NVARCHAR(255) UNIQUE,
        "litellm_params" NCLOB,
        "search_tool_info" NCLOB,
        "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''',

    # differs from LiteLLM_DailyUserSpend by exactly one leading column
    # (end_user_id instead of user_id) - reuses _DAILY_SPEND_COLS (SA-2329).
    "LiteLLM_DailyEndUserSpend": '''CREATE COLUMN TABLE "LiteLLM_DailyEndUserSpend" (
        "id" NVARCHAR(255) PRIMARY KEY,
        "end_user_id" NVARCHAR(255),''' + _DAILY_SPEND_COLS,

    # Archival mirror of LiteLLM_VerificationToken (SA-2329) - column set must stay
    # a superset of its source; see migrations.ensure_mirror_coverage.
    "LiteLLM_DeletedVerificationToken": '''CREATE COLUMN TABLE "LiteLLM_DeletedVerificationToken" (
        "id" NVARCHAR(255) PRIMARY KEY,
        "token" NVARCHAR(255),
        "key_name" NVARCHAR(255),
        "key_alias" NVARCHAR(255),
        "soft_budget_cooldown" BOOLEAN DEFAULT FALSE,
        "spend" DOUBLE DEFAULT 0,
        "expires" TIMESTAMP,
        "models" NCLOB,
        "aliases" NCLOB,
        "config" NCLOB,
        "user_id" NVARCHAR(255),
        "team_id" NVARCHAR(255),
        "agent_id" NVARCHAR(255),
        "project_id" NVARCHAR(255),
        "permissions" NCLOB,
        "max_parallel_requests" INTEGER,
        "metadata" NCLOB,
        "blocked" BOOLEAN,
        "tpm_limit" BIGINT,
        "rpm_limit" BIGINT,
        "max_budget" DOUBLE,
        "budget_duration" NVARCHAR(255),
        "budget_reset_at" TIMESTAMP,
        "allowed_cache_controls" NCLOB,
        "allowed_routes" NCLOB,
        "policies" NCLOB,
        "access_group_ids" NCLOB,
        "model_spend" NCLOB,
        "model_max_budget" NCLOB,
        "router_settings" NCLOB,
        "budget_id" NVARCHAR(255),
        "organization_id" NVARCHAR(255),
        "object_permission_id" NVARCHAR(255),
        "created_at" TIMESTAMP,
        "created_by" NVARCHAR(255),
        "updated_at" TIMESTAMP,
        "updated_by" NVARCHAR(255),
        "last_active" TIMESTAMP,
        "rotation_count" INTEGER DEFAULT 0,
        "auto_rotate" BOOLEAN DEFAULT FALSE,
        "rotation_interval" NVARCHAR(255),
        "last_rotation_at" TIMESTAMP,
        "key_rotation_at" TIMESTAMP,
        "deleted_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "deleted_by" NVARCHAR(255),
        "deleted_by_api_key" NVARCHAR(255),
        "litellm_changed_by" NVARCHAR(255)
    )''',

    # Archival mirror of LiteLLM_TeamTable (SA-2329) - column set must stay a
    # superset of its source; see migrations.ensure_mirror_coverage.
    "LiteLLM_DeletedTeamTable": '''CREATE COLUMN TABLE "LiteLLM_DeletedTeamTable" (
        "id" NVARCHAR(255) PRIMARY KEY,
        "team_id" NVARCHAR(255),
        "team_alias" NVARCHAR(5000),
        "organization_id" NVARCHAR(255),
        "object_permission_id" NVARCHAR(255),
        "admins" NCLOB,
        "members" NCLOB,
        "members_with_roles" NCLOB,
        "metadata" NCLOB,
        "max_budget" DOUBLE,
        "soft_budget" DOUBLE,
        "spend" DOUBLE DEFAULT 0,
        "models" NCLOB,
        "max_parallel_requests" INTEGER,
        "tpm_limit" BIGINT,
        "rpm_limit" BIGINT,
        "budget_duration" NVARCHAR(255),
        "budget_reset_at" TIMESTAMP,
        "blocked" BOOLEAN DEFAULT FALSE,
        "model_spend" NCLOB,
        "model_max_budget" NCLOB,
        "router_settings" NCLOB,
        "team_member_permissions" NCLOB,
        "access_group_ids" NCLOB,
        "policies" NCLOB,
        "model_id" INTEGER,
        "allow_team_guardrail_config" BOOLEAN DEFAULT FALSE,
        "created_at" TIMESTAMP,
        "updated_at" TIMESTAMP,
        "deleted_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "deleted_by" NVARCHAR(255),
        "deleted_by_api_key" NVARCHAR(255),
        "litellm_changed_by" NVARCHAR(255)
    )''',

    # SA-2332: routable via client.py's ALL_MODEL_TABLES since SA-1488 but never
    # given DDL - the Admin UI's SSO/UI Settings pages 500 on Save. Both are
    # 4-column singletons keyed by a fixed id (see proxy_setting_endpoints.py);
    # shape copied from LiteLLM_SearchToolsTable (SA-2324) minus auto_uuid - the
    # callers supply the primary key explicitly, so an auto-generated uuid would
    # mint a second, unreachable row (see client.py's MODEL_SPECS entries).
    "LiteLLM_SSOConfig": '''CREATE COLUMN TABLE "LiteLLM_SSOConfig" (
        "id" NVARCHAR(255) PRIMARY KEY,
        "sso_settings" NCLOB,
        "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''',

    "LiteLLM_UISettings": '''CREATE COLUMN TABLE "LiteLLM_UISettings" (
        "id" NVARCHAR(255) PRIMARY KEY,
        "ui_settings" NCLOB,
        "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''',
}

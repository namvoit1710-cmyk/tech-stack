"""HanaClient — a drop-in substitute for LiteLLM's PrismaClient (SA-1488).

Exposes what the ~110 proxy call sites use:
  * awaitable ``connect`` / ``disconnect`` / ``health_check``
  * a ``.db.<model>.<op>()`` accessor (Prisma model API)
  * the PrismaClient helper methods ``get_data`` / ``get_generic_data`` /
    ``insert_data`` / ``update_data`` / ``delete_data`` and the
    ``spend_log_transactions`` batch-writer attributes.

hdbcli is synchronous, so ops are wrapped with ``asyncio.to_thread``. Selected
at startup via ``DB_BACKEND=hana`` and constructed where PrismaClient is today.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any, List, Optional

from .config import HanaConfig
from .connection import HanaConnectionManager
from .records import HanaRecord
from .repository import HanaRepository, TableSpec
from .schema import CORE_TABLE_DDL

_log = logging.getLogger("litellm.proxy.db.hana")

# --- Best-effort PostgreSQL -> SAP HANA rewrite for the proxy's raw SQL --------
# The proxy issues ~40 hand-written analytics/spend queries through query_raw in
# PostgreSQL dialect. We translate the constructs it actually emits; anything
# still unsupported is caught at execute time and degraded to an empty result
# (see HanaDB.query_raw) so an exotic analytics query never 500s a UI tab.
_JSON_ACCESS = re.compile(r"(\"?\w+\"?)((?:\s*->>?\s*'[^']+')+)")
_EXTRACT_EPOCH = re.compile(
    r"EXTRACT\s*\(\s*EPOCH\s+FROM\s*\(\s*(.+?)\s*-\s*(.+?)\s*\)\s*\)",
    re.IGNORECASE | re.DOTALL,
)
_PG_CAST = re.compile(r"::\s*\w+(?:\s*\[\s*\])?")
_POS_PARAM = re.compile(r"\$\d+")
# " AT TIME ZONE 'UTC'" — the proxy's timestamps are already UTC, so drop it.
_AT_TIME_ZONE = re.compile(r"\s+AT\s+TIME\s+ZONE\s+'[^']*'", re.IGNORECASE)
# (X + INTERVAL '1 day') -> ADD_DAYS(X, 1)
_INTERVAL_DAYS = re.compile(
    r"\(\s*([^()]+?)\s*\+\s*INTERVAL\s+'(\d+)\s+days?'\s*\)", re.IGNORECASE
)
# date_trunc('day', X) -> HANA date/timestamp truncation
_DATE_TRUNC = re.compile(r"date_trunc\s*\(\s*'(\w+)'\s*,\s*([^()]+?)\s*\)", re.IGNORECASE)
# Postgres GROUPING(a, b, c) is a multi-arg bitmask; HANA GROUPING() takes one column.
_GROUPING = re.compile(r"\bGROUPING\s*\(\s*([^()]+?)\s*\)", re.IGNORECASE)


def _repl_grouping(m: "re.Match[str]") -> str:
    args = [a.strip() for a in m.group(1).split(",") if a.strip()]
    if len(args) <= 1:
        return m.group(0)  # single-arg GROUPING is valid in HANA
    # Postgres bitmask: first arg is the most-significant bit.
    n = len(args)
    terms = []
    for i, a in enumerate(args):
        mult = 1 << (n - 1 - i)
        terms.append("GROUPING({}) * {}".format(a, mult) if mult > 1 else "GROUPING({})".format(a))
    return "(" + " + ".join(terms) + ")"

# Known column names (lower -> exact), harvested from the schema DDL. The proxy's
# raw SQL references most columns BARE (e.g. request_id); Postgres folds those to
# lowercase (matching), but HANA folds them to UPPERCASE (mismatch vs our quoted
# lowercase columns). So we quote bare lowercase column refs to their exact name.
_KNOWN_COLUMNS = {}
for _ddl in CORE_TABLE_DDL.values():
    for _col in re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', _ddl):
        if not _col.startswith("LiteLLM"):  # skip the table names in the DDL
            _KNOWN_COLUMNS.setdefault(_col.lower(), _col)
# Tokenizer: a single-quoted string, OR a double-quoted identifier, OR a bare word.
# Only the bare-word group is eligible for quoting (strings/quoted idents pass through).
_SQL_TOKEN = re.compile(r"'(?:[^']|'')*'|\"[^\"]*\"|([A-Za-z_][A-Za-z0-9_]*)")


def _quote_known_columns(q: str) -> str:
    def _repl(m: "re.Match[str]") -> str:
        w = m.group(1)
        if w is None:  # a string literal or an already-quoted identifier
            return m.group(0)
        exact = _KNOWN_COLUMNS.get(w.lower())
        if exact is not None and w == w.lower():  # only bare lowercase refs
            return '"' + exact + '"'
        return w

    return _SQL_TOKEN.sub(_repl, q)


def _translate_json_paths(q: str) -> str:
    # metadata->>'k'  and  metadata->'a'->>'b'  -> JSON_VALUE(metadata, '$.k' / '$.a.b')
    def _repl(m: "re.Match[str]") -> str:
        keys = re.findall(r"->>?\s*'([^']+)'", m.group(2))
        return "JSON_VALUE({}, '$.{}')".format(m.group(1), ".".join(keys))

    return _JSON_ACCESS.sub(_repl, q)


def _repl_date_trunc(m: "re.Match[str]") -> str:
    # Return ISO *strings* (not DATE objects): the analytics endpoints consume the
    # bucket via datetime.fromisoformat(row[...]), matching Postgres/Prisma's output.
    unit = m.group(1).lower()
    expr = m.group(2).strip()
    if unit == "month":
        return "TO_VARCHAR({}, 'YYYY-MM-01')".format(expr)
    if unit == "year":
        return "TO_VARCHAR({}, 'YYYY-01-01')".format(expr)
    if unit == "hour":
        return "TO_VARCHAR({}, 'YYYY-MM-DD HH24:00:00')".format(expr)
    # day (and week/other -> day granularity)
    return "TO_VARCHAR({}, 'YYYY-MM-DD')".format(expr)


def _pg_to_hana_sql(query: str) -> str:
    # EXTRACT(EPOCH FROM (a - b)) -> SECONDS_BETWEEN(b, a)  (HANA arg order = later, earlier)
    q = _EXTRACT_EPOCH.sub(
        lambda m: "SECONDS_BETWEEN({}, {})".format(m.group(2).strip(), m.group(1).strip()),
        query,
    )
    q = _translate_json_paths(q)
    q = _PG_CAST.sub("", q)                   # ::timestamptz, ::date, ::INTEGER, ... -> drop
    q = _AT_TIME_ZONE.sub("", q)              # drop AT TIME ZONE 'UTC' (params already UTC)
    q = _INTERVAL_DAYS.sub(                   # (X + INTERVAL 'N day') -> ADD_DAYS(X, N)
        lambda m: "ADD_DAYS({}, {})".format(m.group(1).strip(), m.group(2)), q
    )
    q = _DATE_TRUNC.sub(_repl_date_trunc, q)  # date_trunc('day', X) -> TO_VARCHAR(X, ...)
    q = _GROUPING.sub(_repl_grouping, q)      # GROUPING(a,b,c) bitmask -> HANA per-col sum
    q = _quote_known_columns(q)               # bare snake_case cols -> "quoted" (HANA folds bare->UPPER)
    q = _POS_PARAM.sub("?", q)                # $1,$2,... -> hdbcli qmark params
    return q


def _is_degradable_sql_error(exc: Exception) -> bool:
    """True for HANA SQL syntax/semantic errors (safe to degrade to empty),
    but NOT connection/operational errors (which must propagate)."""
    cls = type(exc)
    name = "{}.{}".format(getattr(cls, "__module__", ""), getattr(cls, "__name__", ""))
    if "hdbcli" in name and "ProgrammingError" in name:
        return True
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    # 257 syntax, 259 invalid table, 260 invalid column, 261/266/267 type,
    # 328 invalid name, 362 invalid schema
    return code in frozenset({257, 259, 260, 261, 266, 267, 328, 362})

# Prisma exposes models as lowercased attributes: prisma.db.litellm_verificationtoken
MODEL_SPECS = {
    "litellm_config": TableSpec(
        "LiteLLM_Config", "param_name", json_cols=frozenset({"param_value"})
    ),
    "litellm_verificationtoken": TableSpec(
        "LiteLLM_VerificationToken",
        "token",
        json_cols=frozenset({
            "models", "aliases", "config", "router_settings", "permissions", "metadata",
            "allowed_cache_controls", "allowed_routes", "policies", "access_group_ids",
            "model_spend", "model_max_budget", "budget_limits",
        }),
        bool_cols=frozenset({"soft_budget_cooldown", "blocked", "auto_rotate"}),
    ),
    "litellm_usertable": TableSpec(
        "LiteLLM_UserTable",
        "user_id",
        json_cols=frozenset({
            "teams", "models", "metadata", "allowed_cache_controls", "policies",
            "model_spend", "model_max_budget",
        }),
    ),
    "litellm_teamtable": TableSpec(
        "LiteLLM_TeamTable",
        "team_id",
        json_cols=frozenset({
            "admins", "members", "members_with_roles", "metadata", "models", "model_spend",
            "model_max_budget", "router_settings", "team_member_permissions",
            "access_group_ids", "policies", "default_team_member_models", "budget_limits",
        }),
        bool_cols=frozenset({"blocked", "allow_team_guardrail_config"}),
        auto_uuid=frozenset({"team_id"}),
    ),
    "litellm_spendlogs": TableSpec(
        "LiteLLM_SpendLogs",
        "request_id",
        json_cols=frozenset({
            "metadata", "request_tags", "messages", "response", "proxy_server_request",
        }),
    ),
    "litellm_credentialstable": TableSpec(
        "LiteLLM_CredentialsTable",
        "credential_id",
        json_cols=frozenset({"credential_values", "credential_info"}),
        auto_uuid=frozenset({"credential_id"}),
    ),
    "litellm_proxymodeltable": TableSpec(
        "LiteLLM_ProxyModelTable",
        "model_id",
        json_cols=frozenset({"litellm_params", "model_info"}),
        bool_cols=frozenset({"blocked"}),
        auto_uuid=frozenset({"model_id"}),
    ),
    "litellm_budgettable": TableSpec(
        "LiteLLM_BudgetTable", "budget_id",
        json_cols=frozenset({"model_max_budget", "allowed_models"}),
        auto_uuid=frozenset({"budget_id"}),
    ),
    "litellm_organizationtable": TableSpec(
        "LiteLLM_OrganizationTable", "organization_id",
        json_cols=frozenset({"metadata", "models", "model_spend"}),
        auto_uuid=frozenset({"organization_id"}),
    ),
    "litellm_objectpermissiontable": TableSpec(
        "LiteLLM_ObjectPermissionTable", "object_permission_id",
        json_cols=frozenset({
            "mcp_servers", "mcp_access_groups", "mcp_tool_permissions", "vector_stores",
            "agents", "agent_access_groups", "models", "blocked_tools", "mcp_toolsets",
            "search_tools",
        }),
        bool_cols=frozenset({"mcp_tool_search_enabled"}),
        auto_uuid=frozenset({"object_permission_id"}),
    ),
    "litellm_endusertable": TableSpec(
        "LiteLLM_EndUserTable", "user_id",
        bool_cols=frozenset({"blocked"}),
    ),
    "litellm_tagtable": TableSpec(
        "LiteLLM_TagTable", "tag_name",
        json_cols=frozenset({"models", "model_info"}),
    ),
    "litellm_dailyuserspend": TableSpec(
        "LiteLLM_DailyUserSpend", "id",
        auto_uuid=frozenset({"id"}),
    ),
    "litellm_dailyteamspend": TableSpec(
        "LiteLLM_DailyTeamSpend", "id", auto_uuid=frozenset({"id"}),
    ),
    "litellm_dailytagspend": TableSpec(
        "LiteLLM_DailyTagSpend", "id", auto_uuid=frozenset({"id"}),
    ),
    "litellm_dailyorganizationspend": TableSpec(
        "LiteLLM_DailyOrganizationSpend", "id", auto_uuid=frozenset({"id"}),
    ),
    "litellm_teammembership": TableSpec(
        "LiteLLM_TeamMembership", "user_id",
    ),
    "litellm_organizationmembership": TableSpec(
        "LiteLLM_OrganizationMembership", "user_id",
    ),
    "litellm_invitationlink": TableSpec(
        "LiteLLM_InvitationLink", "id",
        bool_cols=frozenset({"is_accepted"}),
        auto_uuid=frozenset({"id"}),
    ),
    "litellm_searchtoolstable": TableSpec(
        "LiteLLM_SearchToolsTable", "search_tool_id",
        json_cols=frozenset({"litellm_params", "search_tool_info"}),
        auto_uuid=frozenset({"search_tool_id"}),
    ),
    "litellm_dailyenduserspend": TableSpec(
        "LiteLLM_DailyEndUserSpend", "id", auto_uuid=frozenset({"id"}),
    ),
    "litellm_deletedverificationtoken": TableSpec(
        "LiteLLM_DeletedVerificationToken",
        "id",
        json_cols=frozenset({
            "models", "aliases", "config", "router_settings", "permissions", "metadata",
            "allowed_cache_controls", "allowed_routes", "policies", "access_group_ids",
            "model_spend", "model_max_budget",
        }),
        bool_cols=frozenset({"soft_budget_cooldown", "blocked", "auto_rotate"}),
        auto_uuid=frozenset({"id"}),
    ),
    "litellm_deletedteamtable": TableSpec(
        "LiteLLM_DeletedTeamTable",
        "id",
        json_cols=frozenset({
            "admins", "members", "members_with_roles", "metadata", "models", "model_spend",
            "model_max_budget", "router_settings", "team_member_permissions",
            "access_group_ids", "policies",
        }),
        bool_cols=frozenset({"blocked", "allow_team_guardrail_config"}),
        auto_uuid=frozenset({"id"}),
    ),
    # SA-2332: json_cols is required - proxy_setting_endpoints.py's GET handlers
    # call dict(record.sso_settings) / expect ui_settings decoded, not a raw NCLOB
    # string. auto_uuid must stay empty - both callers upsert with an explicit,
    # fixed id ("sso_config" / "ui_settings"); auto-generating one would mint a
    # second row that find_unique(id=...) can never see.
    "litellm_ssoconfig": TableSpec(
        "LiteLLM_SSOConfig", "id",
        json_cols=frozenset({"sso_settings"}),
    ),
    "litellm_uisettings": TableSpec(
        "LiteLLM_UISettings", "id",
        json_cols=frozenset({"ui_settings"}),
    ),
}

# PrismaClient helper table_name literals -> Prisma model attribute
_TABLE_TO_MODEL = {
    "user": "litellm_usertable",
    "users": "litellm_usertable",
    "key": "litellm_verificationtoken",
    "keys": "litellm_verificationtoken",
    "config": "litellm_config",
    "spend": "litellm_spendlogs",
    "team": "litellm_teamtable",
    "credential": "litellm_credentialstable",
    "model": "litellm_proxymodeltable",
    "budget": "litellm_budgettable",
    "enduser": "litellm_endusertable",
    "end_user": "litellm_endusertable",
    "organization": "litellm_organizationtable",
    "user_notification": "litellm_usernotifications",
}

# Every Prisma model in schema.prisma (Prisma exposes each as its lowercased
# name). The 7 detailed specs above cover the write-heavy core flows; any other
# model resolves to a generic, read-tolerant handle so the proxy never 500s on
# an unspecced table (SA-1488).
_ALL_MODELS = [
    "LiteLLM_BudgetTable", "LiteLLM_CredentialsTable", "LiteLLM_ProxyModelTable",
    "LiteLLM_AgentsTable", "LiteLLM_OrganizationTable", "LiteLLM_ModelTable",
    "LiteLLM_TeamTable", "LiteLLM_ProjectTable", "LiteLLM_DeletedTeamTable",
    "LiteLLM_UserTable", "LiteLLM_ObjectPermissionTable", "LiteLLM_MCPServerTable",
    "LiteLLM_MCPToolsetTable", "LiteLLM_MCPUserCredentials", "LiteLLM_MCPUserEnvVars",
    "LiteLLM_VerificationToken", "LiteLLM_JWTKeyMapping",
    "LiteLLM_DeprecatedVerificationToken", "LiteLLM_DeletedVerificationToken",
    "LiteLLM_EndUserTable", "LiteLLM_TagTable", "LiteLLM_Config", "LiteLLM_SpendLogs",
    "LiteLLM_ErrorLogs", "LiteLLM_UserNotifications", "LiteLLM_TeamMembership",
    "LiteLLM_OrganizationMembership", "LiteLLM_InvitationLink", "LiteLLM_AuditLog",
    "LiteLLM_DailyUserSpend", "LiteLLM_DailyOrganizationSpend", "LiteLLM_DailyEndUserSpend",
    "LiteLLM_DailyAgentSpend", "LiteLLM_DailyTeamSpend", "LiteLLM_DailyTagSpend",
    "LiteLLM_CronJob", "LiteLLM_ManagedFileTable", "LiteLLM_ManagedObjectTable",
    "LiteLLM_ManagedVectorStoreTable", "LiteLLM_ManagedVectorStoresTable",
    "LiteLLM_GuardrailsTable", "LiteLLM_DailyGuardrailMetrics", "LiteLLM_DailyPolicyMetrics",
    "LiteLLM_SpendLogGuardrailIndex", "LiteLLM_SpendLogToolIndex", "LiteLLM_PromptTable",
    "LiteLLM_HealthCheckTable", "LiteLLM_SearchToolsTable", "LiteLLM_SSOConfig",
    "LiteLLM_ManagedVectorStoreIndexTable", "LiteLLM_CacheConfig", "LiteLLM_UISettings",
    "LiteLLM_ConfigOverrides", "LiteLLM_SkillsTable", "LiteLLM_PolicyTable",
    "LiteLLM_PolicyAttachmentTable", "LiteLLM_ToolTable", "LiteLLM_AccessGroupTable",
    "LiteLLM_ClaudeCodePluginTable", "LiteLLM_MemoryTable", "LiteLLM_AdaptiveRouterState",
    "LiteLLM_AdaptiveRouterSession", "LiteLLM_WorkflowRun", "LiteLLM_WorkflowEvent",
    "LiteLLM_WorkflowMessage",
]
ALL_MODEL_TABLES = {name.lower(): name for name in _ALL_MODELS}


def _looks_hashed(token: Any) -> bool:
    """A LiteLLM virtual key is 'sk-...'; a stored token is its SHA-256 hex."""
    return not (isinstance(token, str) and token.startswith("sk-"))


class HanaModelHandle:
    """Async facade over a HanaRepository (mirrors Prisma's model client)."""

    def __init__(self, repo: HanaRepository):
        self._repo = repo

    async def find_unique(self, where=None, include=None, **kw):
        return await asyncio.to_thread(self._repo.find_unique, where)

    async def find_first(self, where=None, order=None, include=None, **kw):
        return await asyncio.to_thread(self._repo.find_first, where, order)

    async def find_many(self, where=None, order=None, take=None, skip=None, include=None, **kw):
        return await asyncio.to_thread(self._repo.find_many, where, order, take, skip)

    async def create(self, data=None, include=None, **kw):
        return await asyncio.to_thread(self._repo.create, data if data is not None else kw.get("data"))

    async def update(self, where=None, data=None, include=None, **kw):
        return await asyncio.to_thread(self._repo.update, where, data)

    async def upsert(self, where=None, data=None, create=None, update=None, include=None, **kw):
        c = create if create is not None else (data or {}).get("create")
        u = update if update is not None else (data or {}).get("update")
        return await asyncio.to_thread(self._repo.upsert, where, c, u)

    async def delete(self, where=None, include=None, **kw):
        return await asyncio.to_thread(self._repo.delete, where)

    async def delete_many(self, where=None, **kw):
        return await asyncio.to_thread(self._repo.delete_many, where)

    async def count(self, where=None, **kw):
        return await asyncio.to_thread(self._repo.count, where)

    async def create_many(self, data=None, skip_duplicates=False, **kw):
        return await asyncio.to_thread(self._repo.create_many, data or [], skip_duplicates)

    async def update_many(self, where=None, data=None, **kw):
        return await asyncio.to_thread(self._repo.update_many, where, data)

    async def group_by(self, by=None, where=None, having=None, order=None,
                       sum=None, count=None, min=None, max=None, avg=None, **kw):
        return await asyncio.to_thread(
            lambda: self._repo.group_by(
                by=by, where=where, having=having, order=order,
                sum=sum, count=count, min=min, max=max, avg=avg,
            )
        )


class _BatchModelProxy:
    """`batcher.<model>` — queues a model op instead of executing it."""

    def __init__(self, batcher, model_name):
        object.__setattr__(self, "_batcher", batcher)
        object.__setattr__(self, "_model_name", model_name)

    def __getattr__(self, method):
        if method.startswith("__") and method.endswith("__"):
            raise AttributeError(method)

        def _queue(**kwargs):
            self._batcher._ops.append((self._model_name, method, kwargs))

        return _queue


class _HanaBatcher:
    """Stand-in for Prisma's ``db.batch_()`` — queues model ops, runs them on exit.

    Autocommit backend, so there's no atomic batch; the queued writes execute in
    order on ``__aexit__`` (skipped if the block raised). Enough for LiteLLM's
    spend-update batches (SA-1488).
    """

    def __init__(self, db):
        self._db = db
        self._ops = []

    def __getattr__(self, model_name):
        if model_name.startswith("__") and model_name.endswith("__"):
            raise AttributeError(model_name)
        return _BatchModelProxy(self, model_name)

    async def commit(self):
        # Explicit Prisma batch form: b = db.batch_(); b.<model>.<op>(...); await b.commit()
        ops, self._ops = self._ops, []
        for model_name, method, kwargs in ops:
            handle = getattr(self._db, model_name)
            await getattr(handle, method)(**kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        # Context-manager form: `async with db.batch_() as b: ...` -> commit on clean exit.
        if exc_type is None:
            await self.commit()
        else:
            self._ops = []  # discard queued ops if the block raised
        return False


class _HanaTx:
    """Async-CM stand-in for Prisma's ``db.tx()`` on the autocommit HANA backend.

    Yields the db so ``async with prisma_client.db.tx() as t: await t.<model>.<op>()``
    works. No BEGIN/COMMIT (the connection is autocommit); enclosed writes still
    execute. Sufficient for LiteLLM's transactional helpers (SA-1488).
    """

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class HanaDB:
    """Namespace exposing model handles as attributes, like Prisma's client."""

    def __init__(self, mgr: HanaConnectionManager):
        object.__setattr__(self, "_mgr", mgr)
        object.__setattr__(self, "_handles", {})

    def __getattr__(self, name: str) -> HanaModelHandle:
        handles = object.__getattribute__(self, "_handles")
        if name in handles:
            return handles[name]
        spec = MODEL_SPECS.get(name)
        if spec is None:
            table = ALL_MODEL_TABLES.get(name)
            if table is None:
                raise AttributeError(f"HanaDB has no model handle '{name}'")
            # Generic handle for an unspecced model: reads tolerate a not-yet-
            # created table (return empty). Write-heavy core tables use the
            # detailed specs in MODEL_SPECS.
            spec = TableSpec(table, "id")
        mgr = object.__getattribute__(self, "_mgr")
        handles[name] = HanaModelHandle(HanaRepository(mgr, spec))
        return handles[name]

    async def query_raw(self, query: str, *args):
        def _run():
            sql = _pg_to_hana_sql(query)
            params = list(args)
            with self._mgr.cursor() as cur:
                try:
                    cur.execute(sql, params) if params else cur.execute(sql)
                except Exception as e:  # noqa: BLE001
                    if _is_degradable_sql_error(e):
                        _log.warning(
                            "HANA query_raw: unsupported PostgreSQL degraded to empty "
                            "result [%s]. Analytics view shows no data. Query head: %s",
                            e, " ".join(query.split())[:140],
                        )
                        return []
                    raise
                if cur.description is None:
                    return []
                # HANA folds bare (unquoted) result aliases to UPPERCASE; Postgres
                # (which the callers target) folds them to lowercase. Normalize
                # all-uppercase names back to lowercase, but preserve mixed-case
                # names (quoted columns like "startTime") so both styles match.
                cols = [
                    (d[0].lower() if isinstance(d[0], str) and d[0].isupper() else d[0])
                    for d in cur.description
                ]
                return [HanaRecord(zip(cols, r)) for r in cur.fetchall()]
        return await asyncio.to_thread(_run)

    async def execute_raw(self, query: str, *args):
        def _run():
            sql = _pg_to_hana_sql(query)
            params = list(args)
            with self._mgr.cursor() as cur:
                try:
                    cur.execute(sql, params) if params else cur.execute(sql)
                except Exception as e:  # noqa: BLE001
                    if _is_degradable_sql_error(e):
                        _log.warning(
                            "HANA execute_raw: unsupported PostgreSQL skipped [%s]. "
                            "Query head: %s", e, " ".join(query.split())[:140],
                        )
                        return 0
                    raise
                return getattr(cur, "rowcount", -1)
        return await asyncio.to_thread(_run)

    def tx(self, *args, **kwargs):
        # Prisma transaction API: `async with prisma_client.db.tx() as t: ...`
        return _HanaTx(self)

    def batch_(self, *args, **kwargs):
        # Prisma batch API: `async with db.batch_() as batcher: batcher.<model>.<op>(...)`
        return _HanaBatcher(self)


class HanaClient:
    """Drop-in for PrismaClient, backed by SAP HANA."""

    # Class-level batch-writer state, mirroring PrismaClient's contract.
    spend_log_transactions: List = []

    def __init__(self, database_url: Optional[str] = None, proxy_logging_obj: Any = None,
                 http_client: Any = None, config: Optional[HanaConfig] = None):
        self.proxy_logging_obj = proxy_logging_obj
        self._config = config or HanaConfig.from_env()
        self._mgr = HanaConnectionManager(self._config)
        self.db = HanaDB(self._mgr)
        self._spend_log_transactions_lock = asyncio.Lock()

    async def connect(self):
        await asyncio.to_thread(self._mgr.connect)
        await asyncio.to_thread(self._ensure_schema)
        return self

    def _ensure_schema(self):
        """Create any missing HANA tables on startup (idempotent) so a fresh
        deploy self-provisions its schema. Tolerates concurrent-instance races.

        Also verifies already-deployed tables against their current DDL
        (SA-2239): ``ensure_no_drift`` logs an ERROR naming the table and the
        missing column(s) before raising, so a silent column-drift is surfaced
        even though the ``except`` below downgrades the raise to a WARNING to
        keep tolerating the startup race/permission errors this was for.

        ``ensure_ddl_coverage`` (SA-2324) is a separate, coarser gate: it names
        every model ``client.py`` can route that has no DDL in ``TABLE_ORDER``
        at all, so that gap is a startup log line instead of a user-facing 500
        the first time someone exercises an uncovered feature. Detection only
        — it never raises, so it can't turn an accepted gap into a boot failure.

        ``ensure_mirror_coverage`` (SA-2329) guards the archival mirror tables
        (LiteLLM_DeletedVerificationToken, LiteLLM_DeletedTeamTable) against
        drifting from the live table each one archives — a gap neither of the
        two checks above can see, since both reason about a table against its
        OWN DDL. Also detection only.
        """
        try:
            from .migrations import (
                apply_schema,
                ensure_ddl_coverage,
                ensure_mirror_coverage,
                ensure_no_drift,
            )
            from .schema import CORE_TABLE_DDL, TABLE_ORDER

            report = apply_schema(self._mgr, CORE_TABLE_DDL, TABLE_ORDER)
            if report.get("created"):
                _log.info("HANA schema auto-provision: created %s", report["created"])
            ensure_no_drift(self._mgr, CORE_TABLE_DDL, TABLE_ORDER)
            ensure_ddl_coverage(ALL_MODEL_TABLES, TABLE_ORDER)
            ensure_mirror_coverage(CORE_TABLE_DDL)
        except Exception as e:  # noqa: BLE001 — never block boot on schema race/perm
            _log.warning("HANA schema auto-provision skipped: %s", e)

    async def disconnect(self):
        await asyncio.to_thread(self._mgr.close)

    async def health_check(self) -> bool:
        return await asyncio.to_thread(self._mgr.health_check)

    def hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def jsonify_object(self, data: dict) -> dict:
        # The repository encodes JSON/array columns on write; passthrough here.
        return dict(data)

    def jsonify_team_object(self, db_data: dict) -> dict:
        # Mirrors PrismaClient.jsonify_team_object. members_with_roles/budget_limits
        # are in the team spec's json_cols, and repository._encode only serializes
        # non-str values, so a passthrough is correct — pre-encoding would double-wrap.
        return dict(db_data)

    def get_request_status(self, payload) -> str:
        """Mirror PrismaClient.get_request_status: 'failure' if metadata.status is
        'failure', else 'success'. Pure payload parse (no DB). REQUIRED by the daily-
        spend accumulation path — without it that path raises AttributeError (silently
        swallowed at DEBUG), leaving the DailyUserSpend rollup empty so the Usage tab
        never updates on new requests."""
        try:
            md = payload.get("metadata", {})
            if isinstance(md, str):
                md = json.loads(md)
            return "failure" if md.get("status") == "failure" else "success"
        except (json.JSONDecodeError, AttributeError):
            return "success"

    def _model(self, table_name: Optional[str]) -> HanaModelHandle:
        key = table_name or ""
        attr = _TABLE_TO_MODEL.get(key)
        if attr is not None:
            return getattr(self.db, attr)
        # Fall back: the proxy sometimes passes a model-handle attribute name
        # directly (e.g. "litellm_dailyuserspend"). Resolve it (or a litellm_<name>
        # form) to a generic handle so background spend/daily writes don't crash.
        for cand in (key, "litellm_" + key, "litellm_" + key + "table"):
            if cand:
                try:
                    return getattr(self.db, cand)
                except AttributeError:
                    continue
        raise ValueError(f"HanaClient: unsupported table_name {table_name!r}")

    # --- PrismaClient helper-method surface (used across the proxy) ---
    async def get_generic_data(self, key: str, value: Any, table_name: str):
        return await self._model(table_name).find_first(where={key: value})

    async def get_data(self, token=None, user_id=None, team_id=None, key_val=None,
                       table_name=None, query_type="find_unique",
                       expires=None, reset_at=None, budget_id_list=None, **kwargs):
        # Infer the target table when none is given. `combined_view` (auth token
        # lookup) and token/key_val -> key; otherwise route by the id provided, so
        # get_data(user_id=...) returns the USER row (not a key row) — this powers
        # /user/info's spend, budget, etc.
        if table_name not in (None, "combined_view"):
            tn = table_name
        elif token is not None or key_val is not None or table_name == "combined_view":
            tn = "key"
        elif user_id is not None:
            tn = "user"
        elif team_id is not None:
            tn = "team"
        else:
            tn = "key"
        model = self._model(tn)
        # Budget-reset / cleanup selection (mirrors PrismaClient.get_data): the reset
        # job calls find_all with reset_at/expires/budget_id_list to fetch ONLY the
        # rows whose budget cycle has expired. Rows with budget_reset_at = NULL (no
        # budget) fail `< reset_at` and are excluded — without this we returned every
        # row and the reset job zeroed all spend each cycle.
        if query_type == "find_all" and (reset_at is not None or expires is not None or budget_id_list is not None):
            sel: dict = {}
            if reset_at is not None:
                sel["budget_reset_at"] = {"lt": reset_at}
            if expires is not None:
                sel["OR"] = [{"expires": None}, {"expires": {"gt": expires}}]
            if budget_id_list is not None:
                sel["budget_id"] = {"in": budget_id_list}
            return await model.find_many(where=sel)
        where: Optional[dict] = None
        if key_val:
            where = dict(key_val)
        elif isinstance(token, str) and tn == "key":
            where = {"token": token if _looks_hashed(token) else self.hash_token(token)}
        elif user_id is not None:
            where = {"user_id": user_id}
        elif team_id is not None:
            where = {"team_id": team_id}
        if query_type == "find_all":
            return await model.find_many(where=where)
        return await model.find_first(where=where)

    async def insert_data(self, data: dict, table_name: str):
        payload = dict(data)
        if table_name == "key" and "token" in payload and not _looks_hashed(payload["token"]):
            payload["token"] = self.hash_token(payload["token"])
        attr = _TABLE_TO_MODEL.get(table_name or "")
        if attr is None:
            raise ValueError(f"HanaClient: unsupported table_name {table_name!r}")
        handle = getattr(self.db, attr)
        pk = MODEL_SPECS[attr].pk
        pk_val = payload.get(pk)
        if pk_val is not None:
            # create-if-not-exists (matches PrismaClient.insert_data upsert semantics)
            return await handle.upsert(where={pk: pk_val}, create=payload, update={})
        return await handle.create(data=payload)

    async def update_data(self, token=None, data=None, user_id=None, team_id=None,
                         query_type="update", table_name=None, update_key_values=None, **kwargs):
        db_data = dict(data or {})
        # keys -> update by (hashed) token
        if token is not None:
            t = token if _looks_hashed(token) else self.hash_token(token)
            db_data["token"] = t
            row = await self.db.litellm_verificationtoken.update(where={"token": t}, data=db_data)
            return {"token": t, "data": (row.model_dump() if row is not None else {})}
        # users -> upsert by user_id (Prisma create-or-update; the admin user may not exist yet)
        if user_id is not None or table_name == "user":
            uid = user_id if user_id is not None else db_data.get("user_id")
            upd = dict(update_key_values) if update_key_values is not None else db_data
            row = await self.db.litellm_usertable.upsert(
                where={"user_id": uid}, create={**db_data, "user_id": uid}, update=upd,
            )
            return {"user_id": uid, "data": row}
        # teams -> upsert by team_id
        if team_id is not None or table_name == "team":
            tid = team_id if team_id is not None else db_data.get("team_id")
            upd = dict(update_key_values) if update_key_values is not None else db_data
            create_data = {**db_data}
            create_data.setdefault("team_id", tid)
            row = await self.db.litellm_teamtable.upsert(
                where={"team_id": tid}, create=create_data, update=upd,
            )
            return {"team_id": tid, "data": row}
        # generic update
        model = self._model(table_name)
        return await model.update(where=({"user_id": user_id} if user_id else {}), data=db_data)

    async def delete_data(self, tokens=None, team_id_list=None, table_name=None, user_id=None):
        # Prisma callers omit table_name for these deletes; infer it from the
        # selector: tokens->key, team_id_list->team, user_id->user.
        tn = table_name
        if tn is None:
            tn = "key" if tokens else ("team" if team_id_list else ("user" if user_id is not None else None))
        model = self._model(tn)
        if tokens:
            # Caller (delete_verification_tokens) expects the list of deleted tokens.
            deleted_tokens = []
            for t in tokens:
                h = t if _looks_hashed(t) else self.hash_token(t)
                await model.delete(where={"token": h})
                deleted_tokens.append(h)
            return deleted_tokens
        if user_id is not None:
            await model.delete(where={"user_id": user_id})
            return {"deleted_rows": 1}
        if team_id_list:
            for tid in team_id_list:
                await model.delete(where={"team_id": tid})
            return {"deleted_rows": len(team_id_list)}
        return {"deleted_rows": 0}

    async def get_all_latest_health_checks(self):
        # No persisted background health-check history on this backend; UI health tab shows empty.
        return []

    # Boot-path stubs (Prisma-specific extras). HANA-native views are a follow-up.
    async def check_view_exists(self):
        return None

    async def _set_spend_logs_row_count_in_proxy_state(self):
        return None

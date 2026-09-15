"""Design-time functions for the jira-worker (dropdown population + connection test).

Invoked from the builder UI via ``POST /api/v1/functions/{name}/invoke`` with
``{"params": {...__credentials..., "credential_ref": ...}}``. Each reads the SAME
vault credential the node uses at runtime (``resolve_effective_auth``), so a picker
populates against the selected Jira site.

The JiraClient is synchronous; these async handlers offload it to a thread so they
don't block the worker event loop. Functions whose options depend on another field
(``list_transitions`` needs an issue key; ``list_assignable_users`` a project/issue)
are best-effort until the platform serverOptions POST-args gap (SA-1551) lands —
they still work when the dependency is supplied.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from worker_sdk.layer1_domain.entities.worker_function import WorkerFunction
from worker_sdk.layer2_application.services.effective_auth import resolve_effective_auth

from app.layer2_application.services.jira_client import JiraClient, JiraError


def _client(params: Dict[str, Any]) -> JiraClient:
    cred = resolve_effective_auth(params, require=True)
    return JiraClient.from_credential(cred)


async def test_connection(params: Dict[str, Any]) -> Dict[str, Any]:
    """Verify the Jira credential by calling GET /myself."""
    try:
        client = await asyncio.to_thread(_client, params)
        me = await asyncio.to_thread(client.myself)
    except (JiraError, ValueError) as e:
        return {
            "success": False,
            "error": str(e),
            "error_code": getattr(e, "error_code", "AUTH_ERROR"),
        }
    return {
        "success": True,
        "account_id": me.get("accountId"),
        "display_name": me.get("displayName"),
        "message": f"Connected as {me.get('displayName')}",
    }


async def list_projects(params: Dict[str, Any]) -> Dict[str, Any]:
    """List Jira projects for a project picker."""
    try:
        client = await asyncio.to_thread(_client, params)
        query = (params.get("query") or "").strip() or None
        data = await asyncio.to_thread(client.list_projects, query)
    except (JiraError, ValueError) as e:
        return {"success": False, "error": str(e), "options": []}
    options = [
        {"id": p.get("key"), "value": f"{p.get('key')} - {p.get('name')}"}
        for p in (data or {}).get("values", [])
    ]
    return {"success": True, "options": options}


async def list_issue_types(params: Dict[str, Any]) -> Dict[str, Any]:
    """List Jira issue types (non-subtask) for an issue-type picker."""
    try:
        client = await asyncio.to_thread(_client, params)
        data = await asyncio.to_thread(client.list_issue_types)
    except (JiraError, ValueError) as e:
        return {"success": False, "error": str(e), "options": []}
    seen: set = set()
    options = []
    for it in data or []:
        if it.get("subtask"):
            continue
        name = it.get("name")
        if name and name not in seen:
            seen.add(name)
            options.append({"id": name, "value": name})
    return {"success": True, "options": options}


async def list_transitions(params: Dict[str, Any]) -> Dict[str, Any]:
    """List available transitions for an issue (depends on issue_key)."""
    issue_key = (params.get("issue_key") or "").strip()
    if not issue_key:
        return {"success": False, "error": "'issue_key' is required", "options": []}
    try:
        client = await asyncio.to_thread(_client, params)
        data = await asyncio.to_thread(client.get_transitions, issue_key)
    except (JiraError, ValueError) as e:
        return {"success": False, "error": str(e), "options": []}
    options = [
        {"id": t.get("id"), "value": t.get("name")}
        for t in (data or {}).get("transitions", [])
    ]
    return {"success": True, "options": options}


async def list_assignable_users(params: Dict[str, Any]) -> Dict[str, Any]:
    """Search assignable users (scoped by project_key or issue_key)."""
    try:
        client = await asyncio.to_thread(_client, params)
        data = await asyncio.to_thread(
            client.search_assignable_users,
            (params.get("query") or "").strip(),
            (params.get("project_key") or "").strip() or None,
            (params.get("issue_key") or "").strip() or None,
        )
    except (JiraError, ValueError) as e:
        return {"success": False, "error": str(e), "options": []}
    options = [
        {"id": u.get("accountId"), "value": u.get("displayName")}
        for u in (data or [])
    ]
    return {"success": True, "options": options}


# -- WorkerFunction definitions ----------------------------------------------

_OPT_OUT = [{"key": "success", "type": "boolean"}, {"key": "options", "type": "array"}]

test_connection_function = WorkerFunction(
    name="test_connection",
    description="Verify the Jira credential by calling GET /rest/api/3/myself.",
    input_schema=[],
    output_schema=[{"key": "success", "type": "boolean"}, {"key": "display_name", "type": "string"}],
    handler=test_connection,
)
list_projects_function = WorkerFunction(
    name="list_projects",
    description="List Jira projects for a project picker.",
    input_schema=[{"key": "query", "type": "string", "required": False, "label": "Filter"}],
    output_schema=_OPT_OUT,
    handler=list_projects,
)
list_issue_types_function = WorkerFunction(
    name="list_issue_types",
    description="List Jira issue types for an issue-type picker.",
    input_schema=[],
    output_schema=_OPT_OUT,
    handler=list_issue_types,
)
list_transitions_function = WorkerFunction(
    name="list_transitions",
    description="List available transitions for an issue (design-time picker; depends on issue_key).",
    input_schema=[{"key": "issue_key", "type": "string", "required": True, "label": "Issue Key"}],
    output_schema=_OPT_OUT,
    handler=list_transitions,
)
list_assignable_users_function = WorkerFunction(
    name="list_assignable_users",
    description="Search assignable users (design-time picker; scope by project_key/issue_key).",
    input_schema=[{"key": "query", "type": "string", "required": False, "label": "Search"}],
    output_schema=_OPT_OUT,
    handler=list_assignable_users,
)

JIRA_WORKER_FUNCTIONS = [
    test_connection_function,
    list_projects_function,
    list_issue_types_function,
    list_transitions_function,
    list_assignable_users_function,
]

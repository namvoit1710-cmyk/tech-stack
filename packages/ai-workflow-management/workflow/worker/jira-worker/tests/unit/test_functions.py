"""Unit tests for the design-time worker functions (dropdowns + connection test)."""

from app import functions as fns

CRED = {
    "__credentials": {"default": {"type": "json", "config": {"domain": "https://laidon.atlassian.net", "email": "e@x.com", "apiToken": "t"}}},
    "credential_ref": "default",
}


def test_worker_functions_registered():
    names = {f.name for f in fns.JIRA_WORKER_FUNCTIONS}
    assert names == {
        "test_connection",
        "list_projects",
        "list_issue_types",
        "list_transitions",
        "list_assignable_users",
    }


async def test_test_connection_success(rec):
    rec.set("/myself", {"accountId": "a1", "displayName": "Edgar"})
    out = await fns.test_connection(dict(CRED))
    assert out["success"] is True
    assert out["display_name"] == "Edgar"


async def test_test_connection_no_credential():
    out = await fns.test_connection({})
    assert out["success"] is False
    assert out["error_code"] in ("AUTH_ERROR", "VALIDATION_ERROR")


async def test_list_projects_options(rec):
    rec.set("/project/search", {"values": [{"key": "SA", "name": "SimpleMDG"}]})
    out = await fns.list_projects(dict(CRED))
    assert out["success"] is True
    assert out["options"][0]["id"] == "SA"
    assert "SimpleMDG" in out["options"][0]["value"]


async def test_list_issue_types_dedups_and_skips_subtasks(rec):
    rec.set("/issuetype", [{"name": "Task"}, {"name": "Task"}, {"name": "Sub-task", "subtask": True}])
    out = await fns.list_issue_types(dict(CRED))
    assert [o["id"] for o in out["options"]] == ["Task"]


async def test_list_transitions_requires_issue_key():
    out = await fns.list_transitions(dict(CRED))
    assert out["success"] is False
    assert "issue_key" in out["error"]


async def test_list_transitions_ok(rec):
    rec.set("/issue/SA-1/transitions", {"transitions": [{"id": "31", "name": "Done"}]})
    params = dict(CRED)
    params["issue_key"] = "SA-1"
    out = await fns.list_transitions(params)
    assert out["options"][0] == {"id": "31", "value": "Done"}


async def test_list_assignable_users(rec):
    rec.set("/user/assignable/search", [{"accountId": "a1", "displayName": "Edgar"}])
    out = await fns.list_assignable_users(dict(CRED))
    assert out["options"][0] == {"id": "a1", "value": "Edgar"}

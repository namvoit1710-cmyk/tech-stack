"""Unit tests for the 22 Jira node types + their handlers.

Each handler is driven end-to-end (handler -> JiraClient -> patched _request) with a
valid injected credential. Handlers must RAISE on failure (the multi-type route maps
that to TaskStatus.ERROR) and RETURN a dict on success.
"""

import base64
import json
from collections import Counter

import pytest

from worker_sdk import NodeKind

from app.node_types import NODE_TYPES

BY_TYPE = {n.worker_type: n for n in NODE_TYPES}


def _inputs(cred_inputs, **fields):
    return {**cred_inputs, **fields}


def _run(worker_type, cred_inputs, **fields):
    return BY_TYPE[worker_type].handler(_inputs(cred_inputs, **fields), {})


# -- structure ----------------------------------------------------------------


def test_exactly_22_unique_jira_nodes():
    assert len(NODE_TYPES) == 22
    types = [n.worker_type for n in NODE_TYPES]
    assert len(set(types)) == 22
    assert all(t.startswith("jira_") for t in types)


def test_all_nodes_have_handler_and_error_outputs():
    for n in NODE_TYPES:
        assert callable(n.handler), n.worker_type
        assert isinstance(n.input_schema, list)
        assert isinstance(n.output_schema, list)
        out_keys = {f["key"] for f in n.output_schema}
        assert {"success", "error", "error_code"} <= out_keys, n.worker_type


def test_schemas_are_json_serializable():
    for n in NODE_TYPES:
        json.dumps(n.input_schema)
        json.dumps(n.output_schema)


def test_node_labels_are_human_readable():
    for n in NODE_TYPES:
        assert n.name.startswith("Jira: ")
        assert n.node_class == "BUSINESS"


# -- issue ops ----------------------------------------------------------------


def test_create_issue(rec, cred_inputs):
    rec.set("/issue", {"key": "SA-101", "id": "1001"})
    out = _run("jira_create_issue", cred_inputs, project_key="SA", issue_type="Task", summary="Hi", description="desc", labels="a,b")
    assert out["success"] is True
    assert out["issue_key"] == "SA-101"
    assert out["issue_url"] == "https://laidon.atlassian.net/browse/SA-101"
    fields = rec.last()["json"]["fields"]
    assert fields["project"] == {"key": "SA"}
    assert fields["issuetype"] == {"name": "Task"}
    assert fields["description"]["type"] == "doc"
    assert fields["labels"] == ["a", "b"]


def test_create_issue_requires_summary(rec, cred_inputs):
    with pytest.raises(ValueError, match="Summary"):
        _run("jira_create_issue", cred_inputs, project_key="SA", issue_type="Task")


def test_get_issue(rec, cred_inputs):
    rec.set("/issue/SA-1", {"key": "SA-1", "fields": {"summary": "S", "status": {"name": "To Do"}}})
    out = _run("jira_get_issue", cred_inputs, issue_key="SA-1")
    assert out["summary"] == "S"
    assert out["status"] == "To Do"


def test_update_issue(rec, cred_inputs):
    out = _run("jira_update_issue", cred_inputs, issue_key="SA-1", summary="New")
    assert out["updated"] is True
    assert rec.last()["json"] == {"fields": {"summary": "New"}}


def test_update_issue_merges_fields_json(rec, cred_inputs):
    out = _run("jira_update_issue", cred_inputs, issue_key="SA-1", fields_json='{"labels": ["x"]}')
    assert out["updated"] is True
    assert rec.last()["json"]["fields"]["labels"] == ["x"]


def test_update_issue_requires_a_field(rec, cred_inputs):
    with pytest.raises(ValueError, match="at least one field"):
        _run("jira_update_issue", cred_inputs, issue_key="SA-1")


def test_delete_issue(rec, cred_inputs):
    out = _run("jira_delete_issue", cred_inputs, issue_key="SA-1", delete_subtasks=True)
    assert out["deleted"] is True
    assert rec.last()["params"] == {"deleteSubtasks": "true"}


def test_assign_issue(rec, cred_inputs):
    out = _run("jira_assign_issue", cred_inputs, issue_key="SA-1", account_id="acc-1")
    assert out["assignee_account_id"] == "acc-1"
    assert rec.last()["json"] == {"accountId": "acc-1"}


def test_assign_issue_unassign(rec, cred_inputs):
    _run("jira_assign_issue", cred_inputs, issue_key="SA-1", account_id="unassign")
    assert rec.last()["json"] == {"accountId": None}


def test_search(rec, cred_inputs):
    rec.set("/search/jql", {"issues": [{"key": "SA-1"}, {"key": "SA-2"}]})
    out = _run("jira_search", cred_inputs, jql="project = SA")
    assert out["count"] == 2
    assert rec.last()["json"]["jql"] == "project = SA"


# -- comments -----------------------------------------------------------------


def test_add_comment(rec, cred_inputs):
    rec.set("/issue/SA-1/comment", {"id": "10"})
    out = _run("jira_add_comment", cred_inputs, issue_key="SA-1", body="hi")
    assert out["comment_id"] == "10"
    assert rec.last()["json"]["body"]["type"] == "doc"


def test_add_comment_requires_body(rec, cred_inputs):
    with pytest.raises(ValueError, match="body"):
        _run("jira_add_comment", cred_inputs, issue_key="SA-1")


def test_get_comments(rec, cred_inputs):
    rec.set("/issue/SA-1/comment", {"comments": [{"id": "1"}]})
    out = _run("jira_get_comments", cred_inputs, issue_key="SA-1")
    assert out["count"] == 1


def test_update_comment(rec, cred_inputs):
    out = _run("jira_update_comment", cred_inputs, issue_key="SA-1", comment_id="5", body="x")
    assert out["comment_id"] == "5"
    assert rec.last()["path"] == "/issue/SA-1/comment/5"


def test_delete_comment(rec, cred_inputs):
    out = _run("jira_delete_comment", cred_inputs, issue_key="SA-1", comment_id="5")
    assert out["deleted"] is True
    assert rec.last()["method"] == "DELETE"


# -- transitions --------------------------------------------------------------


def test_get_transitions(rec, cred_inputs):
    rec.set("/issue/SA-1/transitions", {"transitions": [{"id": "31", "name": "Done"}]})
    out = _run("jira_get_transitions", cred_inputs, issue_key="SA-1")
    assert out["count"] == 1


def test_transition_issue(rec, cred_inputs):
    out = _run("jira_transition_issue", cred_inputs, issue_key="SA-1", transition_id="31", resolution="Done")
    assert out["transitioned"] is True
    assert rec.last()["json"] == {"transition": {"id": "31"}, "fields": {"resolution": {"name": "Done"}}}


# -- worklog ------------------------------------------------------------------


def test_add_worklog(rec, cred_inputs):
    rec.set("/issue/SA-1/worklog", {"id": "w1"})
    out = _run("jira_add_worklog", cred_inputs, issue_key="SA-1", time_spent="3h 30m")
    assert out["worklog_id"] == "w1"
    assert rec.last()["json"]["timeSpent"] == "3h 30m"


def test_get_worklogs(rec, cred_inputs):
    rec.set("/issue/SA-1/worklog", {"worklogs": [{"id": "1"}]})
    out = _run("jira_get_worklogs", cred_inputs, issue_key="SA-1")
    assert out["count"] == 1


# -- links --------------------------------------------------------------------


def test_link_issues(rec, cred_inputs):
    out = _run("jira_link_issues", cred_inputs, inward_issue_key="SA-1", outward_issue_key="SA-2", link_type="Blocks")
    assert out["linked"] is True
    assert rec.last()["json"]["type"] == {"name": "Blocks"}


def test_delete_link(rec, cred_inputs):
    out = _run("jira_delete_link", cred_inputs, link_id="100")
    assert out["deleted"] is True
    assert rec.last()["path"] == "/issueLink/100"


# -- attachments --------------------------------------------------------------


def test_add_attachment_text(rec, cred_inputs):
    rec.set("/issue/SA-1/attachments", [{"id": "att1", "filename": "f.txt"}])
    out = _run("jira_add_attachment", cred_inputs, issue_key="SA-1", filename="f.txt", content_text="hello")
    assert out["attachment_ids"] == ["att1"]
    assert rec.last()["files"]["file"][0] == "f.txt"


def test_add_attachment_base64(rec, cred_inputs):
    rec.set("/issue/SA-1/attachments", [{"id": "att2"}])
    b64 = base64.b64encode(b"bytes").decode()
    out = _run("jira_add_attachment", cred_inputs, issue_key="SA-1", filename="f.bin", content_base64=b64)
    assert out["attachment_ids"] == ["att2"]


def test_add_attachment_requires_content(rec, cred_inputs):
    with pytest.raises(ValueError, match="content"):
        _run("jira_add_attachment", cred_inputs, issue_key="SA-1", filename="f.txt")


def test_add_attachment_bad_base64(rec, cred_inputs):
    with pytest.raises(ValueError, match="base64"):
        _run("jira_add_attachment", cred_inputs, issue_key="SA-1", filename="f", content_base64="!!!notbase64!!!")


def test_get_attachments(rec, cred_inputs):
    rec.set("/issue/SA-1", {"fields": {"attachment": [{"id": "1"}, {"id": "2"}]}})
    out = _run("jira_get_attachments", cred_inputs, issue_key="SA-1")
    assert out["count"] == 2


# -- projects / users ---------------------------------------------------------


def test_list_projects(rec, cred_inputs):
    rec.set("/project/search", {"values": [{"key": "SA", "name": "SimpleMDG"}]})
    out = _run("jira_list_projects", cred_inputs)
    assert out["count"] == 1


def test_get_project(rec, cred_inputs):
    rec.set("/project/SA", {"key": "SA", "name": "SimpleMDG"})
    out = _run("jira_get_project", cred_inputs, project_key="SA")
    assert out["project_name"] == "SimpleMDG"


def test_myself(rec, cred_inputs):
    rec.set("/myself", {"accountId": "a1", "displayName": "Edgar"})
    out = _run("jira_myself", cred_inputs)
    assert out["display_name"] == "Edgar"


def test_search_users(rec, cred_inputs):
    rec.set("/user/assignable/search", [{"accountId": "a1"}, {"accountId": "a2"}])
    out = _run("jira_search_users", cred_inputs, query="ed")
    assert out["count"] == 2


# -- cross-cutting ------------------------------------------------------------


def test_missing_credential_fails_closed(rec):
    with pytest.raises(ValueError, match="authentication is required"):
        BY_TYPE["jira_get_issue"].handler({"issue_key": "SA-1"}, {})


def test_get_issue_requires_key(rec, cred_inputs):
    with pytest.raises(ValueError, match="VALIDATION_ERROR"):
        BY_TYPE["jira_get_issue"].handler(dict(cred_inputs), {})


# -- functional kind ----------------------------------------------------------


def test_kind_split_is_12_9_1_0():
    counts = Counter(n.kind for n in NODE_TYPES)
    assert counts["action"] == 12
    assert counts["read"] == 9
    assert counts["util"] == 1
    assert counts["trigger"] == 0
    assert len(NODE_TYPES) == 22


def test_every_node_has_kind():
    assert all(getattr(n, "kind", None) for n in NODE_TYPES)


def test_every_node_kind_is_a_node_kind_enum_member():
    """Code-review fix (kind-as-Enum-everywhere): jira-worker's node_types.py
    used to pass kind="read" / kind="util" as raw strings. Every node's kind
    must now be an actual NodeKind member, not merely a string that happens to
    compare equal -- this is what closes the vocabulary at the source."""
    for n in NODE_TYPES:
        assert isinstance(n.kind, NodeKind), n.worker_type

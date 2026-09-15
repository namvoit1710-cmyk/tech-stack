"""T5 migration — inline node secrets round-trip to vault credential_refs.

Covers the four credential-bearing worker shapes (http bearer/api_key/basic, agent,
teams, db), idempotency (already-migrated nodes skipped), and dry-run (no issue, no
mutation). The vault issue API is faked — the real one is Part A (dormant).
"""

from types import SimpleNamespace

from app.layer2_application.features.migrate_inline_credentials.use_cases.migrate_inline_credentials import (
    migrate_workflow_inline_credentials,
)


class _FakeIssuer:
    """Records issue calls and hands back deterministic credential ids."""

    def __init__(self):
        self.calls = []

    def __call__(self, *, tenant_id, kind, config):
        self.calls.append({"tenant_id": tenant_id, "kind": kind, "config": config})
        return f"cred_{len(self.calls)}"


def _node(node_id="n1", **params):
    # parameters is the FE-facing holder where authored inline secrets live.
    return SimpleNamespace(id=node_id, parameters=dict(params), config={}, input_binding={})


def _wf(*nodes):
    return SimpleNamespace(nodes=list(nodes))


def test_http_bearer_migrates_to_credential_ref():
    issue = _FakeIssuer()
    node = _node(auth_type="bearer", auth_token="jwt-123")
    records = migrate_workflow_inline_credentials(_wf(node), tenant_id="t1", issue=issue)

    assert issue.calls == [{"tenant_id": "t1", "kind": "bearer", "config": {"token": "jwt-123"}}]
    assert records[0]["credential_ref"] == "cred_1"
    assert node.parameters["credential_ref"] == "cred_1"
    assert "auth_token" not in node.parameters
    assert node.parameters["auth_type"] == "bearer"  # non-secret hint kept


def test_http_api_key_carries_header():
    issue = _FakeIssuer()
    node = _node(auth_type="api_key", auth_token="k-1", api_key_header="X-Custom")
    migrate_workflow_inline_credentials(_wf(node), tenant_id="t1", issue=issue)

    assert issue.calls[0]["kind"] == "api_key"
    assert issue.calls[0]["config"] == {"value": "k-1", "header": "X-Custom"}
    assert "api_key_header" not in node.parameters


def test_http_basic_migrates_username_password():
    issue = _FakeIssuer()
    node = _node(auth_type="basic", auth_username="u", auth_password="p")
    migrate_workflow_inline_credentials(_wf(node), tenant_id="t1", issue=issue)

    assert issue.calls[0]["config"] == {"username": "u", "password": "p"}
    assert "auth_username" not in node.parameters and "auth_password" not in node.parameters


def test_agent_api_key_migrates():
    issue = _FakeIssuer()
    node = _node(api_key="sk-test")
    migrate_workflow_inline_credentials(_wf(node), tenant_id="t1", issue=issue)

    assert issue.calls[0] == {"tenant_id": "t1", "kind": "api_key", "config": {"value": "sk-test"}}
    assert node.parameters.get("credential_ref") == "cred_1"
    assert "api_key" not in node.parameters


def test_teams_webhook_url_migrates():
    issue = _FakeIssuer()
    node = _node(webhook_url="https://outlook.office.com/webhook/x")
    migrate_workflow_inline_credentials(_wf(node), tenant_id="t1", issue=issue)

    assert issue.calls[0]["kind"] == "webhook_url"
    assert issue.calls[0]["config"] == {"url": "https://outlook.office.com/webhook/x"}
    assert "webhook_url" not in node.parameters


def test_db_username_password_migrates_to_basic():
    issue = _FakeIssuer()
    node = _node(host="db", database="app", username="dbu", password="dbp")
    migrate_workflow_inline_credentials(_wf(node), tenant_id="t1", issue=issue)

    assert issue.calls[0]["kind"] == "basic"
    assert issue.calls[0]["config"] == {"username": "dbu", "password": "dbp"}
    assert "username" not in node.parameters and "password" not in node.parameters
    assert node.parameters["host"] == "db"  # non-secret connection config untouched


def test_already_migrated_node_is_skipped():
    issue = _FakeIssuer()
    node = _node(credential_ref="existing", auth_type="bearer", auth_token="jwt")
    records = migrate_workflow_inline_credentials(_wf(node), tenant_id="t1", issue=issue)

    assert records == []
    assert issue.calls == []  # idempotent: nothing issued


def test_node_without_secret_is_skipped():
    issue = _FakeIssuer()
    node = _node(auth_type="none", url="https://example.com")
    records = migrate_workflow_inline_credentials(_wf(node), tenant_id="t1", issue=issue)

    assert records == []
    assert issue.calls == []


def test_dry_run_reports_without_issuing_or_mutating():
    issue = _FakeIssuer()
    node = _node(api_key="sk-test")
    records = migrate_workflow_inline_credentials(
        _wf(node), tenant_id="t1", issue=issue, dry_run=True
    )

    assert records == [{"node_id": "n1", "kind": "api_key", "dry_run": True}]
    assert issue.calls == []  # nothing issued
    assert node.parameters["api_key"] == "sk-test"  # plaintext untouched
    assert "credential_ref" not in node.parameters

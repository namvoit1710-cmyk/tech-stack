from app.layer1_domain.value_objects.node_kind import NodeKind
from app.layer2_application.providers.worker_node_provider import _build_summary, _worker_collection


def test_family_prefix_maps_to_display_name():
    assert _worker_collection("jira_get_issue") == "Jira"
    assert _worker_collection("jira_create_issue") == "Jira"


def test_standalone_worker_title_cases():
    assert _worker_collection("email") == "Email"
    assert _worker_collection("http_request") == "Http Request"


def test_category_is_collection_dot_kind():
    s = _build_summary({"worker_type": "jira_get_issue", "kind": "read", "name": "Get Issue"})
    assert s.category == "Jira.read"
    assert s.kind == "read"
    assert s.collection == "Jira"
    a = _build_summary({"worker_type": "jira_create_issue", "kind": "action"})
    assert a.category == "Jira.action"


def test_missing_kind_defaults_action():
    s = _build_summary({"worker_type": "jira_link_issues"})
    assert s.category == "Jira.action"


def test_defends_against_a_nodekind_enum_slipping_into_the_dict():
    """Code-review follow-up: `worker` is an untyped wire dict here, but if a
    caller (or a future refactor) hands `_build_summary` a NodeKind member
    instead of its plain string value, the category f-string must still
    render "Jira.read", not "Jira.NodeKind.READ" (str-mixin Enum's default
    __str__/__format__)."""
    s = _build_summary({"worker_type": "jira_get_issue", "kind": NodeKind.READ})
    assert s.category == "Jira.read"
    assert "NodeKind" not in s.category

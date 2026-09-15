from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer2_application.providers.business_node_provider import _build_summary, _sanitize_collection


def _nd(**kw):
    return NodeDefinition(tenant_id="t1", name="X", base_worker_id="w", **kw)


def test_category_is_collection_dot_kind():
    s = _build_summary(_nd(collection="My Nodes", kind="read"))
    assert s.category == "My Nodes.read"
    assert s.kind == "read"
    assert s.collection == "My Nodes"


def test_dotted_collection_name_is_sanitized():
    s = _build_summary(_nd(collection="v1.2", kind="action"))
    assert s.category == "v1 2.action"
    assert s.collection == "v1 2"


def test_sanitize_helper_defaults_blank_to_my_nodes():
    assert _sanitize_collection("") == "My Nodes"
    assert _sanitize_collection("Finance") == "Finance"


def test_blank_kind_heals_to_action_and_category_is_plain_string():
    """Code-review follow-up: a NodeDefinition constructed with no explicit
    `kind` heals it via `default_kind()`, which returns a real NodeKind member
    (not a bare str) -- unlike the other tests in this file, which all pass an
    explicit string kind and so never exercise this path.

    This used to render `category="My Nodes.NodeKind.ACTION"` because the
    f-string `f"{collection}.{kind}"` hit a `str, Enum` member's default
    __str__/__format__, not `NodeDefinitionSummary`'s own serialization (which
    was never reached -- category is a plain field built by hand, not by
    pydantic). Assert the real string, not just `== "action"` (NodeKind is a
    str subclass, so a bare equality check would pass even with the bug).
    """
    s = _build_summary(_nd())  # no kind kwarg -> blank -> healed by default_kind()
    assert s.category == "My Nodes.action"
    assert "NodeKind" not in s.category
    assert s.kind == "action"

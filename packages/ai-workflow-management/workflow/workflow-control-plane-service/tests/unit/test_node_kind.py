from app.layer1_domain.value_objects.node_kind import default_kind, VALID_NODE_KINDS


def test_vocab():
    assert VALID_NODE_KINDS == ("trigger", "action", "read", "logic", "human", "transform", "util")


def test_http_get_is_read():
    # TASK is worker-backed (no static kind) -> inferred from the locked verb.
    assert default_kind("TASK", {"method": "GET"}) == "read"


def test_http_post_is_action():
    assert default_kind("TASK", {"method": "POST"}) == "action"


def test_control_flow_is_logic():
    # SWITCH has a static kind (control_flow.branching -> logic); the verb is irrelevant.
    assert default_kind("SWITCH", {}) == "logic"


def test_fallback_is_action():
    assert default_kind("TASK", {}) == "action"

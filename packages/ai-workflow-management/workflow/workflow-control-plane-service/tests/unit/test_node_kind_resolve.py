from app.layer1_domain.value_objects.node_kind import resolve_kind


class _P:
    """Minimal parent stub carrying a resolved kind."""
    def __init__(self, kind):
        self.kind = kind


def test_explicit_kind_wins_over_parent_and_default():
    assert resolve_kind("read", _P("action"), "TASK", {"method": "POST"}) == "read"


def test_inherits_parent_kind_when_no_explicit():
    assert resolve_kind("", _P("read"), "TASK", {"method": "POST"}) == "read"


def test_falls_back_to_default_kind_at_root():
    assert resolve_kind("", None, "TASK", {"method": "GET"}) == "read"
    assert resolve_kind("", None, "TASK", {"method": "POST"}) == "action"


def test_parent_with_empty_kind_falls_back_to_default():
    assert resolve_kind("", _P(""), "CONDITION", {}) == "logic"

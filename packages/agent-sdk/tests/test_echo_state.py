def test_echo_state_has_agent_result_and_confirmed_annotations():
    from app.layer1_domain.echo_state import EchoState

    annotations = EchoState.__annotations__
    assert "agent_result" in annotations
    assert "confirmed" in annotations


def test_echo_state_is_typed_dict_with_base_state_fields():
    from app.layer1_domain.echo_state import EchoState

    base_fields = {
        "message",
        "conv_id",
        "user_id",
        "tenant_id",
        "source",
        "transport_state",
        "parameters",
        "error",
        "error_code",
        "formatted_response",
    }
    annotations = EchoState.__annotations__
    for field in base_fields:
        assert (
            field in annotations
        ), f"Expected inherited field '{field}' missing from EchoState"


def test_echo_state_source_only_defines_echo_specific_fields():
    import ast
    import inspect

    from app.layer1_domain.echo_state import EchoState

    source = inspect.getsource(EchoState)
    tree = ast.parse(source)
    class_def = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "EchoState"
    )
    own_annotations = {
        node.target.id
        for node in ast.walk(class_def)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    base_fields = {
        "message",
        "conv_id",
        "user_id",
        "tenant_id",
        "source",
        "transport_state",
        "parameters",
        "error",
        "error_code",
        "formatted_response",
    }
    duplicated = base_fields & own_annotations
    assert duplicated == set(), f"EchoState source declares base fields: {duplicated}"
    assert "agent_result" in own_annotations
    assert "confirmed" in own_annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _ExampleDefinition:
    name: str = "default"


def _build_example_definition(value):
    payload = dict(value or {})
    return _ExampleDefinition(name=payload.get("name", "default"))


def test_ensure_definition_returns_existing_instance():
    from agent_sdk.layer2_application.utils.definition import ensure_definition

    definition = _ExampleDefinition(name="kept")

    resolved = ensure_definition(
        definition,
        definition_type=_ExampleDefinition,
        factory=_build_example_definition,
    )

    assert resolved is definition


def test_ensure_definition_builds_from_mapping_and_none():
    from agent_sdk.layer2_application.utils.definition import ensure_definition

    mapped = ensure_definition(
        {"name": "mapped"},
        definition_type=_ExampleDefinition,
        factory=_build_example_definition,
    )
    defaulted = ensure_definition(
        None,
        definition_type=_ExampleDefinition,
        factory=_build_example_definition,
    )

    assert mapped == _ExampleDefinition(name="mapped")
    assert defaulted == _ExampleDefinition(name="default")


def test_definition_utils_are_reexported_from_agent_sdk():
    import agent_sdk
    from agent_sdk import ensure_definition

    assert callable(ensure_definition)
    assert "ensure_definition" in agent_sdk.__all__

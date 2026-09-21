def test_agent_base_state_annotations_include_shared_state_fields():
    from agent_sdk.layer1_domain.entities.agent_state import AgentBaseState

    expected_fields = {
        "shared_state",
        "shared_state_key",
        "shared_state_version",
    }

    assert expected_fields.issubset(AgentBaseState.__annotations__)


def test_shared_state_interfaces_are_reexported():
    from agent_sdk.layer2_application.interfaces import ISharedStateRepository

    assert ISharedStateRepository is not None


def test_shared_state_contracts_importable_from_agent_sdk():
    import agent_sdk
    from agent_sdk import (  # noqa: F401
        HanaSharedStateRepository,
        ISharedStateRepository,
        SharedStateDocument,
        SharedStateLockInfo,
        SharedStateRecord,
        extract_state_snapshot,
        merge_state_snapshot,
    )

    expected_exports = {
        "SharedStateDocument",
        "SharedStateLockInfo",
        "SharedStateRecord",
        "ISharedStateRepository",
        "extract_state_snapshot",
        "merge_state_snapshot",
        "HanaSharedStateRepository",
    }

    assert expected_exports.issubset(set(agent_sdk.__all__))

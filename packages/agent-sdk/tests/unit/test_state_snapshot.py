def test_extract_state_snapshot_returns_deep_copied_sdk_fields_by_default():
    from agent_sdk.layer2_application.utils.state_snapshot import extract_state_snapshot

    parent_state = {
        "shared_state": {"steps": [{"id": "step-1"}]},
        "shared_state_key": "workflow-1",
        "shared_state_version": 3,
        "message": "keep-original",
    }

    snapshot = extract_state_snapshot(parent_state)

    assert snapshot == {
        "shared_state": {"steps": [{"id": "step-1"}]},
        "shared_state_key": "workflow-1",
        "shared_state_version": 3,
    }

    snapshot["shared_state"]["steps"][0]["id"] = "mutated"

    assert parent_state["shared_state"]["steps"][0]["id"] == "step-1"


def test_merge_state_snapshot_updates_sdk_fields_and_requested_keys():
    from agent_sdk.layer2_application.utils.state_snapshot import merge_state_snapshot

    parent_state = {
        "shared_state": {"steps": ["draft"]},
        "shared_state_key": "workflow-1",
        "shared_state_version": 1,
        "context_snapshot": {"status": "pending"},
        "message": "keep-original",
    }
    child_state = {
        "shared_state": {"steps": ["draft", "review"]},
        "shared_state_version": 2,
        "context_snapshot": {"status": "complete"},
    }

    merged_state = merge_state_snapshot(
        parent_state,
        child_state,
        keys=("context_snapshot",),
    )

    assert merged_state == {
        "shared_state": {"steps": ["draft", "review"]},
        "shared_state_key": "workflow-1",
        "shared_state_version": 2,
        "context_snapshot": {"status": "complete"},
        "message": "keep-original",
    }

    child_state["context_snapshot"]["status"] = "changed-after-merge"

    assert merged_state["context_snapshot"]["status"] == "complete"

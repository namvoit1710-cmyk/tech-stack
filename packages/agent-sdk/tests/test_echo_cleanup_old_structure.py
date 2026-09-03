import pathlib


def test_features_directory_does_not_exist():
    echo_agent_root = pathlib.Path(__file__).parent.parent / "examples" / "echo_agent"
    features_dir = echo_agent_root / "app" / "layer2_application" / "features"
    assert not features_dir.exists(), (
        f"Old features directory still exists at {features_dir}. "
        "It should have been deleted as part of the clean architecture refactor."
    )

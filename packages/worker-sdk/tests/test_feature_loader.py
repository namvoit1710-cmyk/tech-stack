from worker_sdk.bootstrap import scan_and_load_features


def test_load_features_registers_expected_features():
    registry = scan_and_load_features()
    assert "execute_task_usecase" in registry
    assert "get_worker_info_usecase" in registry

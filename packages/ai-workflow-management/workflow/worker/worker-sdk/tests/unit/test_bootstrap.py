from unittest.mock import patch, MagicMock
import inspect

from worker_sdk.bootstrap import scan_and_load_features, build_app_container


def test_scan_and_load_features_finds_execute_task():
    registry = scan_and_load_features()
    assert "execute_task_usecase" in registry


def test_scan_and_load_features_finds_get_worker_info():
    registry = scan_and_load_features()
    assert "get_worker_info_usecase" in registry


def test_scan_and_load_features_returns_classes():
    registry = scan_and_load_features()
    for name, cls in registry.items():
        assert inspect.isclass(cls), f"{name} should be a class"


def test_build_app_container_has_use_cases():
    container = build_app_container()
    assert "execute_task_usecase" in container
    assert "get_worker_info_usecase" in container


def test_build_app_container_has_dependencies():
    container = build_app_container()
    assert "_dependencies" in container
    deps = container["_dependencies"]
    assert "logger" in deps
    assert "monitor" in deps
    assert "storage" in deps
    assert "input_reader" in deps
    assert "output_writer" in deps
    assert "worker_registry" in deps


def test_build_app_container_use_cases_are_instances():
    container = build_app_container()
    execute_task_uc = container["execute_task_usecase"]
    get_worker_info_uc = container["get_worker_info_usecase"]
    # They should be instances, not classes
    assert not inspect.isclass(execute_task_uc)
    assert not inspect.isclass(get_worker_info_uc)


def test_build_app_container_smart_injection():
    """Each use case only gets the dependencies it asks for."""
    container = build_app_container()
    execute_task_uc = container["execute_task_usecase"]
    get_worker_info_uc = container["get_worker_info_usecase"]

    # execute_task asks for logger, monitor, task_executor
    assert hasattr(execute_task_uc, "logger")
    assert hasattr(execute_task_uc, "monitor")

    # get_worker_info only asks for logger
    assert hasattr(get_worker_info_uc, "logger")


def test_scan_and_load_features_handles_error(monkeypatch, capsys):
    """Errors during feature loading are caught and printed."""
    import pkgutil

    original_iter_modules = pkgutil.iter_modules

    def mock_iter_modules(paths):
        # Return a fake module that will cause an import error
        yield None, "nonexistent_feature", True

    monkeypatch.setattr(pkgutil, "iter_modules", mock_iter_modules)
    registry = scan_and_load_features()
    assert "nonexistent_feature" not in registry
    captured = capsys.readouterr()
    assert "[!] Error" in captured.out

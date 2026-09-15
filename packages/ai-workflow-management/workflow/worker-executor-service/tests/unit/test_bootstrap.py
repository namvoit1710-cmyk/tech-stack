import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from bootstrap import build_app_container, scan_and_load_features


def test_build_app_container_returns_all_features():
    container = build_app_container()
    features = {k: v for k, v in container.items() if not k.startswith("_")}
    assert len(features) == 26  # +lease_tasks +renew_lease +purge_run_tasks +sweep_pull_tasks +queue_stats +sweep_stale_workers (R4) +list_type_replicas +list_worker_replicas (SA-2053 R6) +list_worker_apps +list_app_instances (SA-2047 R14)
    for name, instance in features.items():
        assert hasattr(instance, "execute"), f"{name} missing execute()"


def test_build_app_container_injects_logger():
    container = build_app_container()
    features = {k: v for k, v in container.items() if not k.startswith("_")}
    for name, instance in features.items():
        assert hasattr(instance, "logger"), f"{name} missing logger"


def test_scan_and_load_features_returns_classes():
    registry = scan_and_load_features()
    for name, cls in registry.items():
        assert isinstance(cls, type), f"{name} is not a class"


def test_sweep_stale_workers_gets_registry_store_injected():
    """R4 (SA-2051): the liveness sweep only runs in production if DI injects
    registry_store into SweepStaleWorkersUseCase by parameter name. Every unit and
    integration test constructs the use case directly with the store passed in, so
    a future rename of the bootstrap 'registry_store' key or the use-case param
    would turn the sweep into a silent prod no-op (execute() returns marked=0) with
    all those tests still green. This anchors the REAL DI path: if it breaks, this
    fails."""
    container = build_app_container()
    uc = container["sweep_stale_workers"]
    assert uc.registry_store is not None

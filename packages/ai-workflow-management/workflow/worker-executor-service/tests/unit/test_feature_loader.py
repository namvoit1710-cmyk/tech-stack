import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bootstrap import scan_and_load_features


def test_load_features_registers_expected_features():
    registry = scan_and_load_features()
    assert "execute_task" in registry
    assert "report_task_result" in registry
    assert "report_health" in registry
    assert "start_heartbeat_loop" in registry
    assert "register_worker" in registry
    assert "deregister_worker" in registry
    assert "get_worker" in registry
    assert "list_workers" in registry
    assert "discover_workers" in registry
    assert "route_task" in registry
    assert "worker_heartbeat" in registry
    assert "sdk_register_worker" in registry
    assert "get_worker_schema" in registry
    assert "handle_worker_callback" in registry
    assert "sweep_timed_out_tasks" in registry
    assert "lease_tasks" in registry     # SA-2028 pull delivery
    assert "renew_lease" in registry     # SA-2029 lease renewal
    assert "purge_run_tasks" in registry  # SA-2032 WCP contract
    assert "sweep_pull_tasks" in registry  # SA-2033 pull sweeper
    assert "queue_stats" in registry       # SA-2034 autoscale signal
    assert "sweep_stale_workers" in registry  # SA-2051 (R4) liveness sweeper
    assert "list_worker_replicas" in registry   # SA-2053 (R6) per-type aggregate
    assert "list_type_replicas" in registry     # SA-2053 (R6) per-instance detail
    assert "list_worker_apps" in registry       # SA-2047 (R14) per-app aggregate


def test_load_features_returns_exactly_twentyfour():
    registry = scan_and_load_features()
    assert len(registry) == 26  # +lease_tasks +renew_lease +purge_run_tasks +sweep_pull_tasks +queue_stats +sweep_stale_workers (R4) +list_type_replicas +list_worker_replicas (SA-2053 R6) +list_worker_apps +list_app_instances (SA-2047 R14)


def test_feature_classes_have_execute_method():
    registry = scan_and_load_features()
    for name, cls in registry.items():
        assert hasattr(cls, "execute"), f"{name} UseCase missing execute() method"

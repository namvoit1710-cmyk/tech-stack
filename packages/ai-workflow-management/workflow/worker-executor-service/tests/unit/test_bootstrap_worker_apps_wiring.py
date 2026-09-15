"""SA-2047 R14 (Task 9) — the container exposes the two new worker-app use
cases, wired the same way list_worker_replicas is: real logger, the ONE
shared registry_store instance, and (for the app_config-aware use case) the
real settings singleton so WORKER_STALE_AFTER_SECONDS is honoured instead of
silently defaulting to 120.

Each assertion below is chosen to fail if the wiring it protects is wrong:
- key presence: fails if the entry is never constructed at all.
- isinstance: fails if some other object (e.g. a stub) is bound to the key.
- registry_store identity: fails if the use case got a fresh store instead
  of the one shared with the rest of the container (see the existing
  test_sweep_stale_workers_gets_registry_store_injected anchor test).
- app_config identity: fails if app_config was left at its None default,
  which would silently pin the stale-after window to 120s regardless of
  WORKER_STALE_AFTER_SECONDS.
"""
from app.layer4_frameworks.config.app_config import settings
from app.layer2_application.features.list_worker_apps.use_cases.list_worker_apps_usecase import (
    ListWorkerAppsUseCase,
)
from app.layer2_application.features.list_app_instances.use_cases.list_app_instances_usecase import (
    ListAppInstancesUseCase,
)
from bootstrap import build_app_container


def test_container_exposes_the_worker_app_usecases():
    container = build_app_container()
    assert "list_worker_apps" in container
    assert "list_app_instances" in container


def test_list_worker_apps_wired_like_its_sibling_replica_usecase():
    container = build_app_container()

    uc = container["list_worker_apps"]
    assert isinstance(uc, ListWorkerAppsUseCase)
    # Same shared store as the rest of the container, not a fresh one.
    assert uc.registry_store is container["_registry_store"]
    # Same app_config the neighbouring list_worker_replicas receives, so
    # WORKER_STALE_AFTER_SECONDS is honoured rather than silently defaulted.
    assert uc.app_config is settings


def test_list_app_instances_wired_like_its_sibling_replica_usecase():
    container = build_app_container()

    uc = container["list_app_instances"]
    assert isinstance(uc, ListAppInstancesUseCase)
    assert uc.registry_store is container["_registry_store"]

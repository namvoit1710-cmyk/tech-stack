"""SA-2437 — per-workflow worker-config: repo, resolver ({instance_id,host}), use case."""
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from app.layer1_domain.entities.workflow_worker_config import WorkflowWorkerConfig
from app.layer4_frameworks.persistence.memory.memory_worker_config_repo import (
    MemoryWorkerConfigRepository,
)
from app.layer1_domain.value_objects.instance_selection import (
    resolve_target_instance_id, resolve_target_host, to_run_selection, normalize_selection,
)
from app.layer2_application.features.set_worker_config.use_cases.set_worker_config_use_case import (
    SetWorkerConfigUseCase, SetWorkerConfigCommand,
)
from app.layer2_application.features.get_worker_config.use_cases.get_worker_config_use_case import (
    GetWorkerConfigUseCase, GetWorkerConfigCommand,
)


# --- repo ---------------------------------------------------------------------

def test_memory_repo_upsert_get_delete():
    r = MemoryWorkerConfigRepository()
    assert r.get("wf1") is None
    r.upsert(WorkflowWorkerConfig(workflow_id="wf1", mode="PER_NODE",
                                  selection={"by_node": {"n1": {"instance_id": "i1", "host": "h1"}}}))
    got = r.get("wf1")
    assert got is not None and got.mode == "PER_NODE"
    assert r.delete("wf1") is True
    assert r.get("wf1") is None
    assert r.delete("wf1") is False


# --- resolver with {instance_id, host} object values --------------------------

def test_resolver_object_value_instance_and_host():
    sel = {"by_node": {"n1": {"instance_id": "i-local", "host": "DESKTOP-1"}}}
    assert resolve_target_instance_id(sel, "n1", "wt") == "i-local"
    assert resolve_target_host(sel, "n1", "wt") == "DESKTOP-1"


def test_resolver_legacy_string_value_still_works():
    sel = {"by_worker_type": {"wt": "i-server"}}
    assert resolve_target_instance_id(sel, "n1", "wt") == "i-server"
    assert resolve_target_host(sel, "n1", "wt") == ""


def test_resolver_precedence_node_over_worker_type():
    sel = {
        "by_worker_type": {"wt": {"instance_id": "srv", "host": "H-srv"}},
        "by_node": {"n1": {"instance_id": "loc", "host": "H-loc"}},
    }
    assert resolve_target_instance_id(sel, "n1", "wt") == "loc"
    assert resolve_target_host(sel, "n1", "wt") == "H-loc"


# --- to_run_selection: only the active mode's map -----------------------------

def test_to_run_selection_whole_flow_drops_by_node():
    saved = {"by_worker_type": {"wt": {"instance_id": "i", "host": "h"}},
             "by_node": {"n1": {"instance_id": "x", "host": "y"}}}
    out = to_run_selection("WHOLE_FLOW", saved)
    assert out["by_worker_type"] and out["by_node"] == {}


def test_to_run_selection_per_node_drops_by_worker_type():
    saved = {"by_worker_type": {"wt": {"instance_id": "i", "host": "h"}},
             "by_node": {"n1": {"instance_id": "x", "host": "y"}}}
    out = to_run_selection("PER_NODE", saved)
    assert out["by_node"] and out["by_worker_type"] == {}


def test_to_run_selection_empty_active_map_returns_empty():
    assert to_run_selection("PER_NODE", {"by_worker_type": {"wt": {"instance_id": "i"}}}) == {}


# --- normalize keeps object values, drops empties -----------------------------

def test_normalize_keeps_object_values():
    out = normalize_selection({"mode": "PER_NODE",
                               "by_node": {"n1": {"instance_id": "i1", "host": "h1"}, "n2": {"instance_id": "", "host": ""}}})
    assert out["by_node"] == {"n1": {"instance_id": "i1", "host": "h1"}}


# --- SetWorkerConfig use case -------------------------------------------------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_set_and_get_use_case_roundtrip():
    repo = MemoryWorkerConfigRepository()
    setter = SetWorkerConfigUseCase(worker_config_repo=repo)
    getter = GetWorkerConfigUseCase(worker_config_repo=repo)
    res = _run(setter.execute(SetWorkerConfigCommand(
        workflow_id="wf9", mode="WHOLE_FLOW",
        selection={"by_worker_type": {"claude_check": {"instance_id": "i1", "host": "DESKTOP-1"}}},
    )))
    assert res.error == "" and res.mode == "WHOLE_FLOW"
    cfg = _run(getter.execute(GetWorkerConfigCommand(workflow_id="wf9")))
    assert cfg is not None
    assert cfg.selection["by_worker_type"]["claude_check"]["host"] == "DESKTOP-1"
    assert cfg.updated_at  # timestamp stamped


def test_set_empty_clears_row():
    repo = MemoryWorkerConfigRepository()
    setter = SetWorkerConfigUseCase(worker_config_repo=repo)
    _run(setter.execute(SetWorkerConfigCommand(workflow_id="wf9",
         selection={"by_worker_type": {"wt": {"instance_id": "i"}}})))
    assert repo.get("wf9") is not None
    res = _run(setter.execute(SetWorkerConfigCommand(workflow_id="wf9", selection={})))
    assert res.cleared is True
    assert repo.get("wf9") is None


def test_pin_scoped_to_own_flow_not_subflow():
    # SA-2437: a config tagged with flow A must NOT apply to a sub-flow run (flow B).
    from types import SimpleNamespace
    from app.layer2_application.orchestration.orchestration_engine import _pin_selection_for
    sel = {"workflow_id": "A", "by_worker_type": {"wt": {"instance_id": "i", "host": "h"}}, "by_node": {}}
    parent = SimpleNamespace(workflow_id="A", metadata={"__instance_selection": sel})
    child = SimpleNamespace(workflow_id="B", metadata={"__instance_selection": sel})  # sub-flow
    assert _pin_selection_for(parent) == sel          # applies to its own flow
    assert _pin_selection_for(child) is None          # ignored for the called sub-flow
    # untagged (legacy) selection still applies (back-compat)
    legacy = SimpleNamespace(workflow_id="X", metadata={"__instance_selection": {"by_node": {"n": "i"}}})
    assert _pin_selection_for(legacy) is not None


def test_set_bad_mode_errors():
    repo = MemoryWorkerConfigRepository()
    setter = SetWorkerConfigUseCase(worker_config_repo=repo)
    res = _run(setter.execute(SetWorkerConfigCommand(workflow_id="wf9", mode="BOGUS",
         selection={"by_node": {"n1": {"instance_id": "i"}}})))
    assert res.error

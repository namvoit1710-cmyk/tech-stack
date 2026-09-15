"""SA-2424 — per-run worker-instance selection resolver + normalizer (WCP side)."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from app.layer1_domain.value_objects.instance_selection import (
    resolve_target_instance_id,
    normalize_selection,
    MODE_WHOLE_FLOW,
    MODE_PER_NODE,
)


# --- resolve_target_instance_id: precedence per node ----------------------------

def test_per_node_wins_over_worker_type():
    sel = {
        "by_worker_type": {"email-worker": "server"},
        "by_node": {"n1": "local"},
    }
    # n1 is pinned per-node -> local, even though its worker_type says server
    assert resolve_target_instance_id(sel, "n1", "email-worker") == "local"


def test_worker_type_applies_to_all_nodes_of_type():
    sel = {"by_worker_type": {"email-worker": "server"}}
    assert resolve_target_instance_id(sel, "n1", "email-worker") == "server"
    assert resolve_target_instance_id(sel, "n2", "email-worker") == "server"
    # a different worker_type is unaffected
    assert resolve_target_instance_id(sel, "n3", "http-request") == ""


def test_no_selection_returns_empty():
    assert resolve_target_instance_id(None, "n1", "email-worker") == ""
    assert resolve_target_instance_id({}, "n1", "email-worker") == ""
    assert resolve_target_instance_id("garbage", "n1", "email-worker") == ""


def test_unpinned_node_returns_empty():
    sel = {"by_node": {"n1": "local"}}
    assert resolve_target_instance_id(sel, "n2", "email-worker") == ""


# --- normalize_selection --------------------------------------------------------

def test_normalize_keeps_valid_maps_and_mode():
    out = normalize_selection({
        "mode": MODE_WHOLE_FLOW,
        "by_worker_type": {"email-worker": "server"},
        "by_node": {},
    })
    assert out["mode"] == MODE_WHOLE_FLOW
    assert out["by_worker_type"] == {"email-worker": "server"}


def test_normalize_per_node_mode():
    out = normalize_selection({
        "mode": MODE_PER_NODE,
        "by_node": {"n1": "local", "n2": "server"},
    })
    assert out["mode"] == MODE_PER_NODE
    assert out["by_node"] == {"n1": "local", "n2": "server"}


def test_normalize_empty_returns_empty():
    assert normalize_selection(None) == {}
    assert normalize_selection({}) == {}
    # maps present but empty -> nothing to pin
    assert normalize_selection({"by_worker_type": {}, "by_node": {}}) == {}


def test_normalize_drops_blank_entries():
    out = normalize_selection({"by_node": {"n1": "local", "n2": "", "": "x"}})
    assert out["by_node"] == {"n1": "local"}


def test_normalize_bad_mode_raises():
    with pytest.raises(ValueError):
        normalize_selection({"mode": "BOGUS", "by_node": {"n1": "local"}})


def test_normalize_non_dict_raises():
    with pytest.raises(ValueError):
        normalize_selection("not-a-dict")

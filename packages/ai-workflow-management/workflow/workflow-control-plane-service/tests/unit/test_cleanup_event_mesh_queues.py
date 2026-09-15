"""Filter logic of tools/cleanup_event_mesh_queues.py.

Tests the namespace-strip + regex-match pipeline used to identify orphan
queues. No actual broker interaction; the broker-facing parts (list,
delete) are integration tests of the EventMeshBrokerClient itself.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys

import pytest


# Load the script as a module so we can test its private helpers.
@pytest.fixture(scope="module")
def cleanup_module():
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    script_path = os.path.join(
        repo_root, "tools", "cleanup_event_mesh_queues.py",
    )
    spec = importlib.util.spec_from_file_location(
        "cleanup_event_mesh_queues", script_path,
    )
    mod = importlib.util.module_from_spec(spec)
    # Add a fake sys.argv so argparse doesn't choke during import
    saved = sys.argv
    sys.argv = ["cleanup_event_mesh_queues.py"]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = saved
    return mod


class TestStripNamespace:
    def test_strips_known_prefix(self, cleanup_module):
        result = cleanup_module._strip_namespace(
            "simplemdg/event/core-3/sub-orchestration-responses-7a2c7636",
            "simplemdg/event/core-3",
        )
        assert result == "sub-orchestration-responses-7a2c7636"

    def test_handles_trailing_slash_in_namespace(self, cleanup_module):
        result = cleanup_module._strip_namespace(
            "simplemdg/event/core-3/sub-q",
            "simplemdg/event/core-3/",
        )
        assert result == "sub-q"

    def test_no_namespace_returns_qualified_unchanged(self, cleanup_module):
        result = cleanup_module._strip_namespace("sub-foo", "")
        assert result == "sub-foo"

    def test_non_matching_prefix_returns_qualified_unchanged(self, cleanup_module):
        result = cleanup_module._strip_namespace(
            "other/ns/sub-foo", "simplemdg/event/core-3",
        )
        assert result == "other/ns/sub-foo"


class TestDefaultPatterns:
    """The default patterns should match the orphan formats this codebase
    has produced across the queue-naming evolution and miss healthy ones.
    """

    def test_matches_post_fix_hyphenated_response_queue(self, cleanup_module):
        regexes = [re.compile(p) for p in cleanup_module._DEFAULT_PATTERNS]
        assert cleanup_module._matches(
            "sub-orchestration-responses-7a2c7636", regexes,
        )

    def test_matches_pre_fix_short_uuid_dotted_queue(self, cleanup_module):
        regexes = [re.compile(p) for p in cleanup_module._DEFAULT_PATTERNS]
        assert cleanup_module._matches(
            "sub-orchestration.responses.7a2c7636", regexes,
        )

    def test_matches_pre_fix_full_uuid_dotted_queue(self, cleanup_module):
        regexes = [re.compile(p) for p in cleanup_module._DEFAULT_PATTERNS]
        assert cleanup_module._matches(
            "sub-orchestration.responses.5cf86206-c663-4de7-8bd4-5203c81f6bd8",
            regexes,
        )

    def test_does_not_match_orchestration_actions_queue(self, cleanup_module):
        """The main orchestration.actions subscription queue must NOT
        be matched by the default orphan patterns — it's live."""
        regexes = [re.compile(p) for p in cleanup_module._DEFAULT_PATTERNS]
        assert not cleanup_module._matches(
            "sub-orchestration-actions", regexes,
        )

    def test_does_not_match_workflow_event_queue(self, cleanup_module):
        """The workflow_event audit queue is also live — must not be
        flagged as orphan by the default filter."""
        regexes = [re.compile(p) for p in cleanup_module._DEFAULT_PATTERNS]
        assert not cleanup_module._matches("orchestrator.workflow_event", regexes)


# ---------------------------------------------------------------------------
# A queue NAME is not proof of orphanhood
#
# Run on tenant-1 2026-08-05, this tool raised
# `AttributeError: 'EventMeshBrokerClient' object has no attribute
# '_management_url'` before listing anything — that attribute (and `_namespace`)
# moved into EventMeshAdmin/EventMeshNames in the messaging refactor and the
# script was never updated. The recovery path `ResponseTopicListener.stop()`
# points operators at did not exist.
#
# And it was worse than dead. Two of its three default patterns matched LIVE
# queues: `^sub-[a-zA-Z0-9_-]+\.[a-zA-Z0-9_.-]+$` matches `sub-workflow.tasks`
# (the executor's REST queue AND the rollback target of the AMQP migration),
# and `^sub-orchestration-responses-[0-9a-f]+$` matches the live response queue
# whenever AMQP is off — it missed only because the live one currently carries
# an `-amqp` suffix. Luck, not design.
# ---------------------------------------------------------------------------

import hashlib
import json
from unittest.mock import MagicMock

NS = "simplemdg/event/core-2"
APP = "ai-workflow-control-plane"
LIVE_SHARD = hashlib.sha256(f"{APP}_0".encode()).hexdigest()[:8]


def _broker(**bodies):
    """A broker whose `get_queue` answers from `bodies`, keyed by local name."""
    b = MagicMock()
    b.namespace = NS
    b.list_queues.return_value = [f"{NS}/{n}" for n in bodies]
    b.get_queue.side_effect = lambda q: bodies.get(q.replace(f"{NS}/", ""))
    return b


class TestItRefusesToDeleteLiveQueues:
    def test_the_live_shard_is_protected(self, cleanup_module, monkeypatch):
        """A restart re-adopts this exact queue — deleting it is the outage."""
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": APP}))
        local = f"sub-orchestration-responses-{LIVE_SHARD}"
        b = _broker(**{local: {"consumerCount": 0, "messageCount": 0}})

        ok, reason = cleanup_module.classify(
            b, f"{NS}/{local}", cleanup_module.live_shards(8), local,
        )
        assert ok is False
        assert "live shard" in reason

    def test_a_queue_with_consumers_is_never_an_orphan(self, cleanup_module):
        local = "sub-orchestration-responses-deadbeef"
        b = _broker(**{local: {"consumerCount": 1, "messageCount": 0}})

        ok, reason = cleanup_module.classify(b, f"{NS}/{local}", set(), local)
        assert ok is False
        assert "consumer" in reason

    def test_a_queue_holding_messages_needs_force(self, cleanup_module):
        """A backlog is not litter — and --force is deliberately not --yes."""
        local = "sub-orchestration-responses-deadbeef"
        b = _broker(**{local: {"consumerCount": 0, "messageCount": 42}})

        ok, reason = cleanup_module.classify(b, f"{NS}/{local}", set(), local)
        assert ok is False and "42 message" in reason

        ok, _ = cleanup_module.classify(b, f"{NS}/{local}", set(), local, force=True)
        assert ok is True

    def test_an_unreadable_queue_is_left_alone(self, cleanup_module):
        """Unknown state is not permission to delete."""
        b = _broker()
        b.get_queue.side_effect = lambda _q: None

        ok, reason = cleanup_module.classify(
            b, f"{NS}/sub-orchestration-responses-dead00", set(),
            "sub-orchestration-responses-dead00",
        )
        assert ok is False
        assert "unknown state" in reason

    def test_a_genuine_orphan_is_deletable(self, cleanup_module):
        """Guard the guard: a tool that can never delete anything is useless."""
        local = "sub-orchestration-responses-1520de6d"
        b = _broker(**{local: {"consumerCount": 0, "messageCount": 0}})

        ok, reason = cleanup_module.classify(b, f"{NS}/{local}", {LIVE_SHARD}, local)
        assert ok is True
        assert "orphan" in reason


class TestTheDefaultPatternsNeverSweepAShareQueue:
    @pytest.mark.parametrize("local", [
        "sub-workflow.tasks",             # executor REST queue + rollback target
        "sub-workflow-tasks-amqp",
        "sub-orchestration-actions",
        "sub-realtime-events-amqp",
        "sub-workflow-triggers-amqp-dmq",
        "executor.topic",
        "business.topic.thanh",
    ])
    def test_a_shared_or_live_queue_never_matches(self, cleanup_module, local):
        regexes = [re.compile(p) for p in cleanup_module._DEFAULT_PATTERNS]
        assert not cleanup_module._matches(local, regexes), f"{local} must never be swept"

    @pytest.mark.parametrize("local", [
        "sub-orchestration-responses-1520de6d",
        "sub-orchestration-responses-88297ac0-amqp",
    ])
    def test_a_per_instance_response_queue_matches(self, cleanup_module, local):
        regexes = [re.compile(p) for p in cleanup_module._DEFAULT_PATTERNS]
        assert cleanup_module._matches(local, regexes)


class TestShardDerivation:
    def test_it_matches_what_the_app_computes(self, cleanup_module, monkeypatch):
        """If the tool's derivation and the app's ever diverge, the tool
        deletes live queues."""
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": APP}))
        monkeypatch.setenv("CF_INSTANCE_INDEX", "0")
        from app.layer4_frameworks.config.instance_identity import cf_instance_identity
        from app.layer4_frameworks.orchestration_queue.response_topic_listener import (
            _instance_shard,
        )

        assert _instance_shard(cf_instance_identity()) in cleanup_module.live_shards(8)

    def test_every_replica_index_is_protected(self, cleanup_module, monkeypatch):
        """Scaling down orphans indexes 1..N-1; scaling back UP re-adopts them.
        Protect the whole configured range, not just index 0."""
        monkeypatch.setenv("VCAP_APPLICATION", json.dumps({"application_name": APP}))
        shards = cleanup_module.live_shards(4)

        assert len(shards) == 4
        for i in range(4):
            assert hashlib.sha256(f"{APP}_{i}".encode()).hexdigest()[:8] in shards

    def test_off_cloud_foundry_nothing_is_protected(self, cleanup_module, monkeypatch):
        monkeypatch.delenv("VCAP_APPLICATION", raising=False)
        assert cleanup_module.live_shards(8) == set()

    def test_a_malformed_vcap_does_not_crash_it(self, cleanup_module, monkeypatch):
        monkeypatch.setenv("VCAP_APPLICATION", "{not json")
        assert cleanup_module.live_shards(8) == set()


class TestTheNamespaceMustResolve:
    """`EVENT_MESH_NAMESPACE` is usually unset — the app discovers its namespace
    lazily by parsing the broker's own 403, so a freshly built client has an
    EMPTY one. With an empty namespace nothing is stripped, every local name
    keeps its `<ns>/` prefix, no `^sub-` pattern can match, and the tool prints
    "Nothing matched": it reports *no orphans* without having looked.

    Measured on tenant-1 2026-08-05: 49 queues listed, 0 matched, one live
    per-instance queue present that should have matched-then-been-kept."""

    def test_it_falls_back_to_the_service_binding(self, cleanup_module, monkeypatch):
        monkeypatch.setenv("VCAP_SERVICES", json.dumps({
            "enterprise-messaging": [
                {"credentials": {"namespace": "simplemdg/event/core-2"}}
            ]
        }))
        assert cleanup_module.namespace_from_vcap() == "simplemdg/event/core-2"

    def test_no_binding_yields_empty_not_a_crash(self, cleanup_module, monkeypatch):
        monkeypatch.delenv("VCAP_SERVICES", raising=False)
        assert cleanup_module.namespace_from_vcap() == ""

    def test_malformed_vcap_yields_empty_not_a_crash(self, cleanup_module, monkeypatch):
        monkeypatch.setenv("VCAP_SERVICES", "{not json")
        assert cleanup_module.namespace_from_vcap() == ""

    def test_an_empty_namespace_makes_every_name_unmatchable(self, cleanup_module):
        """The mechanism behind the false negative, stated directly."""
        regexes = [re.compile(p) for p in cleanup_module._DEFAULT_PATTERNS]
        qualified = "simplemdg/event/core-2/sub-orchestration-responses-1520de6d"

        unstripped = cleanup_module._strip_namespace(qualified, "")
        assert not cleanup_module._matches(unstripped, regexes)

        stripped = cleanup_module._strip_namespace(qualified, "simplemdg/event/core-2")
        assert cleanup_module._matches(stripped, regexes)

"""SA-2087 (R12) — per-instance response routing: collision-proof shards,
bounded identities, and cleanup on shutdown.

These tests encode the two defects traced in
``hackathons/sa-2047-worker-replica-hackathon/evidence/sa-2087-r12-instance-id-trace-2026-07-22.md``:

A. SHARD_COLLISION — ``_instance_shard`` truncates (``compact[:8]``), which
   assumes ids differ in their FIRST bytes. True for uuid4, FALSE for a
   Kubernetes Deployment pod name (``<deployment>-<rs-hash>-<random>``), where
   every replica of ``wio-executor`` collapses to the shard ``wioexecu``. With
   ``num_partitions=1`` and one shared ``group.id``, a single pod then consumes
   every peer's responses and drops them silently.

B. UNBOUNDED_QUEUE_SET — each new instance id provisions an Event Mesh queue
   via ``ensure_topic``; ``delete_queue`` exists with zero callers and the
   listener has no ``stop()``, so the queue set grows one per boot forever.

Spec: solace/specs/sa-2087-r12-instance-identity-routing-spec.md
"""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

from app.layer4_frameworks.orchestration_queue.response_registry import ResponseRegistry
from app.layer4_frameworks.orchestration_queue.response_topic_listener import (
    ResponseTopicListener,
    response_topic_for,
)

# Two pod names from the SAME Deployment — this is what Kubernetes actually
# generates for ``metadata.name`` when ``kind: Deployment``. The k8s manifest
# that pinned INSTANCE_ID to it has since been deleted (SA-2272 — nothing
# deployed it, and this repo has no Kubernetes pipeline), but these shapes stay:
# the shard function must survive ANY id shape, which is the property the bug
# violated. Testing it against literals rather than a file is the point.
POD_A = "wio-executor-7d4f9c8b6-x2k9p"
POD_B = "wio-executor-7d4f9c8b6-qq471"

# Even a STABLE k8s identity (a StatefulSet ordinal) shares the same prefix —
# proving the bug is the shard function, not the instability the ticket named.
ORDINAL_0 = "wio-executor-0"
ORDINAL_1 = "wio-executor-1"

# The CF shape after the fix: <app_name>_<CF_INSTANCE_INDEX>.
CF_0 = "wio-executor_0"
CF_1 = "wio-executor_1"


def _sha8(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


class TestShardIsCollisionProof:
    """AC A1 — RED before the fix: both pods shard to 'wioexecu'."""

    def test_two_pods_of_one_deployment_get_different_topics(self):
        assert response_topic_for(POD_A) != response_topic_for(POD_B)

    def test_stable_ordinals_also_get_different_topics(self):
        """Pinning a STABLE id (the ticket's own 'likely direction') does NOT
        fix routing on its own — 'wioexecutor0'[:8] == 'wioexecutor1'[:8] ==
        'wioexecu'. Stability buys a bounded queue set, not correctness.
        """
        assert response_topic_for(ORDINAL_0) != response_topic_for(ORDINAL_1)

    def test_cf_app_qualified_indices_get_different_topics(self):
        assert response_topic_for(CF_0) != response_topic_for(CF_1)

    def test_shard_is_the_sha256_prefix(self):
        """Golden values, computed independently of the implementation. If the
        derivation ever changes, these break loudly rather than drifting.
        """
        assert response_topic_for(POD_A) == "orchestration.responses.cae6db3c"
        assert response_topic_for(POD_B) == "orchestration.responses.8f37b610"
        assert _sha8(POD_A) == "cae6db3c"

    def test_shard_is_deterministic_across_calls(self):
        assert response_topic_for(POD_A) == response_topic_for(POD_A)

    def test_empty_id_still_yields_an_empty_shard(self):
        """Unchanged contract: a blank id must not hash to a real-looking
        shard — blank means 'no instance', and it must stay visibly blank.
        """
        assert response_topic_for("") == "orchestration.responses."


class TestQueueNameBudget:
    """AC A2 — the 60-char Event Mesh local-segment limit still holds."""

    def test_qualified_queue_name_fits_60_chars_for_a_pod_name(self):
        local_queue = f"sub-{response_topic_for(POD_A)}"
        assert len(local_queue) <= 60, f"{local_queue!r} exceeds the 60-char limit"

    def test_shard_length_is_fixed_regardless_of_input_length(self):
        short = response_topic_for("a")
        long = response_topic_for("a" * 500)
        assert len(short) == len(long)


class TestNoCrossInstanceLeakage:
    """AC A3 — a response addressed to A must never resolve B's future."""

    def test_two_listeners_subscribe_to_distinct_topics(self):
        broker_a, broker_b = MagicMock(), MagicMock()
        ResponseTopicListener(broker_a, POD_A, ResponseRegistry()).start()
        ResponseTopicListener(broker_b, POD_B, ResponseRegistry()).start()

        topic_a = broker_a.subscribe_to_topic.call_args.args[0]
        topic_b = broker_b.subscribe_to_topic.call_args.args[0]
        assert topic_a != topic_b


class TestStopReleasesTheQueue:
    """AC A4 — RED before the fix: ``stop()`` does not exist, so every boot
    leaves its Event Mesh queue behind forever.
    """

    @staticmethod
    def _queue_broker() -> MagicMock:
        """A broker that owns queue naming itself — the real
        ``EventMeshBrokerClient.delete_topic_queue(topic)`` takes a TOPIC and
        derives the local queue name internally via ``EventMeshNames``, the
        mirror of ``ensure_topic``. The listener never hand-builds a queue
        name, so there is nothing for it to get wrong.
        """
        return MagicMock(
            spec=[
                "publish_to_topic", "subscribe_to_topic", "ensure_topic",
                "delete_topic_queue",
            ]
        )

    def test_stop_deletes_the_queue_it_provisioned(self):
        broker = self._queue_broker()
        listener = ResponseTopicListener(broker, POD_A, ResponseRegistry())
        listener.start()

        listener.stop()

        broker.delete_topic_queue.assert_called_once_with(response_topic_for(POD_A))

    def test_stop_is_idempotent(self):
        broker = self._queue_broker()
        listener = ResponseTopicListener(broker, POD_A, ResponseRegistry())
        listener.start()

        listener.stop()
        listener.stop()

        assert broker.delete_topic_queue.call_count == 1

    def test_stop_before_start_is_a_safe_no_op(self):
        broker = self._queue_broker()
        listener = ResponseTopicListener(broker, POD_A, ResponseRegistry())

        listener.stop()

        broker.delete_topic_queue.assert_not_called()

    def test_stop_survives_a_broker_failure(self):
        """Mirrors ``start()``'s degrade-don't-crash contract: a shutdown must
        never be blocked by a queue that is already gone.
        """
        broker = self._queue_broker()
        broker.delete_topic_queue.side_effect = RuntimeError("410 Gone")
        listener = ResponseTopicListener(broker, POD_A, ResponseRegistry())
        listener.start()

        listener.stop()  # must not raise

    def test_stop_on_a_broker_without_queue_deletion_is_a_no_op(self):
        """Kafka (dev/local) has ensure_topic but no delete. Shutdown must not
        explode there.
        """
        broker = MagicMock(spec=["publish_to_topic", "subscribe_to_topic", "ensure_topic"])
        listener = ResponseTopicListener(broker, POD_A, ResponseRegistry())
        listener.start()

        listener.stop()  # must not raise


class TestShutdownWiring:
    """AC A5 — RED before the fix: main.py starts the listener (:319-320) and
    the shutdown block never touches it, so the queue always outlives the pod.

    Source-level assertion, following the precedent in
    ``test_loop_value_kind.py:252`` (``inspect.getsource``): driving the real
    lifespan would require the whole broker/HANA/gRPC stack, and the thing
    under test is one wiring line.
    """

    @staticmethod
    def _shutdown_source() -> str:
        import inspect

        import main as main_mod

        src = inspect.getsource(main_mod)
        marker = "# --- Shutdown ---"
        assert marker in src, "shutdown section marker moved — update this test"
        return src.split(marker, 1)[1]

    def test_shutdown_stops_the_response_listener(self):
        assert "response_listener.stop()" in self._shutdown_source()


# ``TestCfManifestIdentity`` lived here. Its whole subject was
# ``deployment-executor.yml``, the CF manifest for ``wio-executor`` — an app no
# pipeline ever built and no CF space ever ran (verified: tenant-1 hosts
# ``ai-workflow-worker-executor`` and ``executor-runtime-service``, never
# ``wio-executor``). The manifest was deleted under SA-2272, so the class went
# with it.
#
# Nothing is lost. Identity no longer depends on any manifest: SA-2271 moved it
# into ``resolve_instance_id``, which derives ``<app>_<index>`` from the CF
# container env and treats an unexpanded ``${...}`` as absent. That behaviour is
# covered by ``test_instance_identity.py``. What remains below is the weaker,
# durable assertion that applies to whatever manifests DO exist.


class TestEveryDeploymentManifestIsInspected:
    """The gap that let a leak ship anyway.

    The guard that used to live above hardcoded ``deployment-executor.yml``.
    The control plane's own manifest, ``deployment.yml``, was never opened by
    any test — and it never got an ``INSTANCE_ID`` at all, so that app kept the
    random per-boot default and leaked one response queue per deploy while CI
    stayed green. A guard that inspects one named file reports on that file,
    not on the deployment.

    Identity no longer *depends* on these manifests (``resolve_instance_id``
    derives it from the CF container env), so the assertion here is the weaker,
    durable one: whatever a manifest does say must not re-pin the churning GUID.
    """

    @staticmethod
    def _manifest_paths() -> list:
        from pathlib import Path

        deployment_dir = Path(__file__).resolve().parents[2] / "deployment"
        assert deployment_dir.is_dir(), f"deployment dir not found at {deployment_dir}"
        return sorted(deployment_dir.glob("deployment*.yml"))

    def test_discovery_covers_every_manifest_present(self):
        """The control plane's own manifest must be inspected, and the sweep
        must keep covering whatever else appears beside it.

        Previously this asserted a hardcoded PAIR, which broke the moment one
        of the two was deleted — a guard that names its files fails when the
        deployment changes, which is the opposite of what a guard is for."""
        names = {p.name for p in self._manifest_paths()}
        assert "deployment.yml" in names, names
        assert len(names) == len(self._manifest_paths()), "duplicate manifest names"

    def test_no_manifest_rebinds_the_churning_guid(self):
        for path in self._manifest_paths():
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.split("#", 1)[0]  # drop comments — they explain the fix
                if "INSTANCE_ID" in line or "CF_INSTANCE_GUID" in line:
                    assert "CF_INSTANCE_GUID" not in line, f"{path.name}: {line!r}"

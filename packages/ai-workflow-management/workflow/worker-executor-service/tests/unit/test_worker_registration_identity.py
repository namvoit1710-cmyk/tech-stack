"""R1 (SA-2048) — per-instance identity on WorkerRegistration.

Identity is a PAIR: `id` = the registration (this worker_type, offered by that
process); `instance_id` = the process. See sa-2047-worker-identity-model-spec.md §2.
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer1_domain.entities.worker_registration import WorkerRegistration


class TestNewFieldDefaults:
    def test_defaults_are_blank_safe(self):
        """proto3-additive: an SDK that sends none of these still registers."""
        w = WorkerRegistration(worker_type="http-request-worker")
        assert w.host == ""
        assert w.pid == 0
        assert w.started_at == ""

    def test_fields_round_trip(self):
        w = WorkerRegistration(
            worker_type="http-request-worker",
            instance_id="pod-1",
            host="worker-http-request-worker-2",
            pid=4711,
            started_at="2026-07-17T09:00:00+00:00",
        )
        assert w.host == "worker-http-request-worker-2"
        assert w.pid == 4711
        assert w.started_at == "2026-07-17T09:00:00+00:00"


class TestIdentityPair:
    """A registration is the PAIR (process x worker_type) — keystone §2.

    Renamed from TestIdentityInvariant: there is no `id == instance_id` invariant.
    That was the rev-2 model, and it made the registration id process-scoped.
    """

    def test_blank_instance_id_adopts_the_generated_id(self):
        """AC2: no process named -> the registration IS its own instance."""
        w = WorkerRegistration(worker_type="http-request-worker")
        assert w.instance_id == w.id
        assert w.instance_id != ""
        # keystone §4: when the process is unknown the id is a bare uuid4 —
        # NOT a derived "uuid4:worker_type" pair. This is the live path today.
        assert ":" not in w.id

    def test_blank_instance_id_adopts_an_explicit_id(self):
        """AC4: protects the HANA store's _row_to_entity, which rehydrates a row
        with id= and no instance_id. A supplied id must survive untouched."""
        w = WorkerRegistration(id="abc", worker_type="http-request-worker")
        assert w.instance_id == "abc"
        assert w.id == "abc"

    def test_supplied_instance_id_derives_the_pair(self):
        """AC1: id = f"{instance_id}:{worker_type}".

        rev-2 asserted `w.id == "pod-1"` here — the process, not the registration.
        A supplied `id=` loses to the derivation: whenever instance_id is given,
        id is a pure function of the (instance_id, worker_type) pair, so the caller
        cannot pin an arbitrary id (keystone §4.1). Note the derivation is NOT
        injective — ("app_0", "jira:create") and ("app_0:jira", "create") both
        derive "app_0:jira:create" — but no producer emits a colon in either half
        today, and SA-2055 must keep it that way.
        """
        w = WorkerRegistration(id="abc", instance_id="pod-1", worker_type="http-request-worker")
        assert w.id == "pod-1:http-request-worker"
        assert w.instance_id == "pod-1"

    def test_one_process_many_types_get_distinct_ids(self):
        """The jira-worker case (keystone §2.1): 22 worker_types register from ONE
        process. rev-2 gave all 22 the id "jira-worker_0" — reproduced as
        `rows in store: 1, UNRESOLVABLE: 21/22` (keystone §3.1). This is the test
        that would have caught it.
        """
        ids = {
            WorkerRegistration(instance_id="jira-worker_0", worker_type=f"jira_t{i}").id
            for i in range(22)
        }
        assert len(ids) == 22
        assert "jira-worker_0:jira_t0" in ids
        assert "jira-worker_0:jira_t21" in ids

    def test_replicas_of_one_type_get_distinct_ids(self):
        """The other half of the pair: 3 replicas of ONE type are 3 registrations.
        Keying on worker_type alone (a CLASS) is the pre-SA-2049 collapse."""
        ids = {
            WorkerRegistration(instance_id=f"jira-worker_{i}", worker_type="jira_t0").id
            for i in range(3)
        }
        assert ids == {
            "jira-worker_0:jira_t0",
            "jira-worker_1:jira_t0",
            "jira-worker_2:jira_t0",
        }

    def test_same_pair_twice_is_the_same_id(self):
        """AC3: the derivation is a pure function of the pair — a restarted replica
        reuses its own row instead of orphaning one (keystone §5)."""
        a = WorkerRegistration(instance_id="p0", worker_type="t")
        b = WorkerRegistration(instance_id="p0", worker_type="t")
        assert a.id == b.id == "p0:t"
        assert a.instance_id == b.instance_id == "p0"


class TestNormalizationStillRuns:
    """AC5: the identity derivation sits AFTER the two normalize_* guards and must
    not shadow them. A registration that names a process is still rejected for an
    invented kind or delivery_mode."""

    def test_unknown_kind_still_raises_with_a_pair(self):
        with pytest.raises(ValueError):
            WorkerRegistration(instance_id="p0", worker_type="t", kind="banana")

    def test_unknown_delivery_mode_still_raises_with_a_pair(self):
        with pytest.raises(ValueError):
            WorkerRegistration(instance_id="p0", worker_type="t", delivery_mode="poll")

    def test_blank_kind_and_mode_normalize_while_the_pair_derives(self):
        """Blank stays proto3-additive: an old worker sends "" and still registers,
        and it still gets a correctly derived id."""
        w = WorkerRegistration(instance_id="p0", worker_type="t", kind="", delivery_mode="")
        assert w.kind == "action"
        assert w.delivery_mode == "push"
        assert w.id == "p0:t"
        assert w.instance_id == "p0"

    def test_normalization_raises_before_identity_is_derived(self):
        """The guards run first, so a bad kind never produces a half-built entity."""
        with pytest.raises(ValueError):
            WorkerRegistration(worker_type="t", kind="write")

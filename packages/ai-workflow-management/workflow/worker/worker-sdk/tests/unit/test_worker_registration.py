import pytest

from worker_sdk.layer1_domain.entities.worker_registration import WorkerRegistration
from worker_sdk.layer1_domain.value_objects.node_kind import NodeKind


def test_worker_registration_kind_default():
    wr = WorkerRegistration(worker_type="demo", version="1", endpoint="http://x")
    assert wr.kind == NodeKind.ACTION


def test_worker_registration_kind_accepts_plain_string_backcompat():
    """A worker outside this repo may still pass kind="read" as a plain
    string -- NodeKind(str, Enum) must accept that at construction."""
    wr = WorkerRegistration(worker_type="demo", version="1", endpoint="http://x", kind="read")
    assert wr.kind == "read"
    assert wr.kind is NodeKind.READ


def test_worker_registration_kind_rejects_invented_kind():
    """"write" is the invented kind that actually shipped -- must raise."""
    with pytest.raises(ValueError, match="kind"):
        WorkerRegistration(worker_type="demo", version="1", endpoint="http://x", kind="write")


def test_worker_registration_kind_rejects_unknown_value():
    with pytest.raises(ValueError, match="kind"):
        WorkerRegistration(worker_type="demo", version="1", endpoint="http://x", kind="banana")

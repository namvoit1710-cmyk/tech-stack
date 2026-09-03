import json

import pytest

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer1_domain.value_objects.task_metrics import TaskMetrics
from worker_sdk.layer1_domain.value_objects.worker_status import WorkerStatus
from worker_sdk.layer1_domain.value_objects.chunk_options import ChunkOptions
from worker_sdk.layer1_domain.value_objects.data_metadata import DataMetadata
from worker_sdk.layer1_domain.value_objects.input_reference import InputReference
from worker_sdk.layer1_domain.value_objects.output_reference import OutputReference
from worker_sdk.layer1_domain.value_objects.write_options import WriteOptions
from worker_sdk.layer1_domain.value_objects.node_kind import NodeKind


# --- TaskStatus ---

def test_task_status_values():
    assert TaskStatus.SUCCESS == "success"
    assert TaskStatus.ERROR == "error"


def test_task_status_is_str():
    assert isinstance(TaskStatus.SUCCESS, str)
    assert isinstance(TaskStatus.ERROR, str)


# --- TaskMetrics ---

def test_task_metrics_defaults():
    m = TaskMetrics()
    assert m.duration_ms == 0.0
    assert m.input_bytes == 0
    assert m.output_bytes == 0


def test_task_metrics_custom_values():
    m = TaskMetrics(duration_ms=150.5, input_bytes=1024, output_bytes=2048)
    assert m.duration_ms == 150.5
    assert m.input_bytes == 1024
    assert m.output_bytes == 2048


# --- WorkerStatus ---

def test_worker_status_values():
    assert WorkerStatus.STARTING == "starting"
    assert WorkerStatus.HEALTHY == "healthy"
    assert WorkerStatus.BUSY == "busy"
    assert WorkerStatus.DRAINING == "draining"
    assert WorkerStatus.UNHEALTHY == "unhealthy"
    assert WorkerStatus.STOPPED == "stopped"


def test_worker_status_is_str():
    assert isinstance(WorkerStatus.HEALTHY, str)


# --- ChunkOptions ---

def test_chunk_options_defaults():
    opts = ChunkOptions()
    assert opts.chunk_size == 1024 * 1024
    assert opts.offset == 0
    assert opts.limit is None


def test_chunk_options_custom():
    opts = ChunkOptions(chunk_size=512, offset=10, limit=100)
    assert opts.chunk_size == 512
    assert opts.offset == 10
    assert opts.limit == 100


# --- DataMetadata ---

def test_data_metadata_defaults():
    meta = DataMetadata()
    assert meta.content_type == "application/octet-stream"
    assert meta.size_bytes is None
    assert meta.checksum is None
    assert meta.extra == {}


def test_data_metadata_custom():
    meta = DataMetadata(
        content_type="text/csv",
        size_bytes=4096,
        checksum="abc123",
        extra={"encoding": "utf-8"},
    )
    assert meta.content_type == "text/csv"
    assert meta.size_bytes == 4096
    assert meta.checksum == "abc123"
    assert meta.extra == {"encoding": "utf-8"}


# --- InputReference ---

def test_input_reference_required():
    ref = InputReference(uri="s3://bucket/key")
    assert ref.uri == "s3://bucket/key"
    assert ref.metadata is None


def test_input_reference_with_metadata():
    meta = DataMetadata(content_type="text/plain")
    ref = InputReference(uri="file:///tmp/data", metadata=meta)
    assert ref.metadata.content_type == "text/plain"


# --- OutputReference ---

def test_output_reference_required():
    ref = OutputReference(uri="s3://out/key")
    assert ref.uri == "s3://out/key"
    assert ref.metadata is None


def test_output_reference_with_metadata():
    meta = DataMetadata(size_bytes=100)
    ref = OutputReference(uri="file:///tmp/out", metadata=meta)
    assert ref.metadata.size_bytes == 100


# --- WriteOptions ---

def test_write_options_defaults():
    opts = WriteOptions()
    assert opts.content_type == "application/octet-stream"
    assert opts.overwrite is False


def test_write_options_custom():
    opts = WriteOptions(content_type="text/plain", overwrite=True)
    assert opts.content_type == "text/plain"
    assert opts.overwrite is True


# --- NodeKind (SA-1734/SA-1735 code-review fix) ---


def test_node_kind_values():
    assert NodeKind.TRIGGER == "trigger"
    assert NodeKind.ACTION == "action"
    assert NodeKind.READ == "read"
    assert NodeKind.LOGIC == "logic"
    assert NodeKind.HUMAN == "human"
    assert NodeKind.TRANSFORM == "transform"
    assert NodeKind.UTIL == "util"


def test_node_kind_is_str():
    assert isinstance(NodeKind.READ, str)


def test_node_kind_accepts_plain_string_backcompat():
    """A worker outside this repo passing kind="read" as a plain string must
    still construct -- NodeKind(str, Enum) is additive, not a breaking type
    change for callers on the old raw-string contract."""
    assert NodeKind("read") is NodeKind.READ
    assert NodeKind("read") == "read"


def test_node_kind_rejects_invented_kind():
    """"write" is the invented kind that actually shipped (documented by the
    executor) despite never being in the vocabulary -- the SDK must reject it
    at construction, the same way the old tuple-membership check did."""
    with pytest.raises(ValueError):
        NodeKind("write")


def test_node_kind_rejects_unknown_value():
    with pytest.raises(ValueError):
        NodeKind("banana")


def test_node_kind_json_dumps_is_plain_string_not_enum_repr():
    """Wire-compat proof: json.dumps must render the plain value ("read"),
    never the Enum repr ("NodeKind.READ") -- str(NodeKind.READ) is the repr
    and must NOT leak onto the wire."""
    assert json.dumps({"kind": NodeKind.READ}) == '{"kind": "read"}'
    assert str(NodeKind.READ) != NodeKind.READ.value

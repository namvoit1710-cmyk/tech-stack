from worker_sdk.layer1_domain.entities.task_request import TaskRequest
from worker_sdk.layer1_domain.entities.task_response import TaskResponse
from worker_sdk.layer1_domain.entities.worker_info import WorkerInfo
from worker_sdk.layer1_domain.entities.worker_registration import WorkerRegistration
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer1_domain.value_objects.task_metrics import TaskMetrics


# --- TaskRequest ---

def test_task_request_required_fields():
    req = TaskRequest(task_id="t1", action="run")
    assert req.task_id == "t1"
    assert req.action == "run"


def test_task_request_defaults():
    req = TaskRequest(task_id="t1", action="run")
    assert req.inputs == {}
    assert req.parameters == {}
    assert req.correlation_id is None


def test_task_request_all_fields():
    req = TaskRequest(
        task_id="t1",
        action="run",
        inputs={"key": "val"},
        parameters={"p": 1},
        correlation_id="corr-1",
    )
    assert req.inputs == {"key": "val"}
    assert req.parameters == {"p": 1}
    assert req.correlation_id == "corr-1"


# --- TaskResponse ---

def test_task_response_required_fields():
    resp = TaskResponse(task_id="t1", status=TaskStatus.SUCCESS)
    assert resp.task_id == "t1"
    assert resp.status == TaskStatus.SUCCESS


def test_task_response_defaults():
    resp = TaskResponse(task_id="t1", status=TaskStatus.ERROR)
    assert resp.outputs == {}
    assert resp.error is None
    assert resp.metrics is None


def test_task_response_all_fields():
    metrics = TaskMetrics(duration_ms=100.0, input_bytes=10, output_bytes=20)
    resp = TaskResponse(
        task_id="t1",
        status=TaskStatus.SUCCESS,
        outputs={"result": "ok"},
        error="none",
        metrics=metrics,
    )
    assert resp.outputs == {"result": "ok"}
    assert resp.error == "none"
    assert resp.metrics.duration_ms == 100.0


# --- WorkerInfo ---

def test_worker_info_required_fields():
    info = WorkerInfo(worker_type="etl", version="1.0")
    assert info.worker_type == "etl"
    assert info.version == "1.0"


def test_worker_info_defaults():
    info = WorkerInfo(worker_type="etl", version="1.0")
    assert info.sdk_version == ""
    assert info.metadata == {}


def test_worker_info_all_fields():
    info = WorkerInfo(
        worker_type="etl",
        version="1.0",
        sdk_version="2.0",
        metadata={"env": "prod"},
    )
    assert info.sdk_version == "2.0"
    assert info.metadata == {"env": "prod"}


# --- WorkerRegistration ---

def test_worker_registration_required_fields():
    reg = WorkerRegistration(
        worker_type="etl", version="1.0", endpoint="http://localhost:35000"
    )
    assert reg.worker_type == "etl"
    assert reg.version == "1.0"
    assert reg.endpoint == "http://localhost:35000"


def test_worker_registration_defaults():
    reg = WorkerRegistration(
        worker_type="etl", version="1.0", endpoint="http://localhost:35000"
    )
    assert reg.sdk_version is None
    assert reg.input_schema is None
    assert reg.output_schema is None


def test_worker_registration_all_fields():
    reg = WorkerRegistration(
        worker_type="etl",
        version="1.0",
        endpoint="http://localhost:35000",
        sdk_version="2.0",
    )
    assert reg.sdk_version == "2.0"


def test_worker_registration_with_schemas():
    input_schema = {"fields": [{"key": "name", "outputType": "string"}]}
    output_schema = {"fields": [{"key": "result", "outputType": "string"}]}
    reg = WorkerRegistration(
        worker_type="etl",
        version="1.0",
        endpoint="http://localhost:35000",
        input_schema=input_schema,
        output_schema=output_schema,
    )
    assert reg.input_schema == input_schema
    assert reg.output_schema == output_schema

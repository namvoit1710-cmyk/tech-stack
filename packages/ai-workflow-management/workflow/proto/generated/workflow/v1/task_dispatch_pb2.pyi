from google.protobuf import struct_pb2 as _struct_pb2
from workflow.v1 import common_pb2 as _common_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class DispatchTaskRequest(_message.Message):
    __slots__ = ("task_id", "run_id", "node_id", "worker_type", "input", "node_config", "idempotency_key")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    NODE_ID_FIELD_NUMBER: _ClassVar[int]
    WORKER_TYPE_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    NODE_CONFIG_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    run_id: str
    node_id: str
    worker_type: str
    input: _struct_pb2.Struct
    node_config: _struct_pb2.Struct
    idempotency_key: str
    def __init__(self, task_id: _Optional[str] = ..., run_id: _Optional[str] = ..., node_id: _Optional[str] = ..., worker_type: _Optional[str] = ..., input: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., node_config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class DispatchTaskResponse(_message.Message):
    __slots__ = ("task_id", "run_id", "status", "output_reference", "output_metadata", "execution_time_ms", "error_message")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_REFERENCE_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_METADATA_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_TIME_MS_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    run_id: str
    status: _common_pb2.TaskStatus
    output_reference: str
    output_metadata: _struct_pb2.Struct
    execution_time_ms: int
    error_message: str
    def __init__(self, task_id: _Optional[str] = ..., run_id: _Optional[str] = ..., status: _Optional[_Union[_common_pb2.TaskStatus, str]] = ..., output_reference: _Optional[str] = ..., output_metadata: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., execution_time_ms: _Optional[int] = ..., error_message: _Optional[str] = ...) -> None: ...

class TaskProgressEvent(_message.Message):
    __slots__ = ("task_id", "event_type", "progress_pct", "message", "data")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    EVENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_PCT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    event_type: str
    progress_pct: float
    message: str
    data: _struct_pb2.Struct
    def __init__(self, task_id: _Optional[str] = ..., event_type: _Optional[str] = ..., progress_pct: _Optional[float] = ..., message: _Optional[str] = ..., data: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

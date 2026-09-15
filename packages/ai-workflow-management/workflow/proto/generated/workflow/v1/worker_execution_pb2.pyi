from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ExecuteRequest(_message.Message):
    __slots__ = ("task_id", "action", "inputs", "parameters", "correlation_id")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    INPUTS_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    action: str
    inputs: _struct_pb2.Struct
    parameters: _struct_pb2.Struct
    correlation_id: str
    def __init__(self, task_id: _Optional[str] = ..., action: _Optional[str] = ..., inputs: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., parameters: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class ExecuteResponse(_message.Message):
    __slots__ = ("task_id", "status", "outputs", "error", "duration_ms", "output_reference")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    OUTPUTS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_REFERENCE_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    status: str
    outputs: _struct_pb2.Struct
    error: str
    duration_ms: float
    output_reference: str
    def __init__(self, task_id: _Optional[str] = ..., status: _Optional[str] = ..., outputs: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., error: _Optional[str] = ..., duration_ms: _Optional[float] = ..., output_reference: _Optional[str] = ...) -> None: ...

class ExecutionEvent(_message.Message):
    __slots__ = ("task_id", "event_type", "progress_pct", "message", "result")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    EVENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_PCT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    event_type: str
    progress_pct: float
    message: str
    result: ExecuteResponse
    def __init__(self, task_id: _Optional[str] = ..., event_type: _Optional[str] = ..., progress_pct: _Optional[float] = ..., message: _Optional[str] = ..., result: _Optional[_Union[ExecuteResponse, _Mapping]] = ...) -> None: ...

class GetInfoRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class WorkerInfoResponse(_message.Message):
    __slots__ = ("worker_type", "version", "sdk_version", "name", "description", "node_class", "icon", "color", "tags", "input_schema", "output_schema", "functions")
    WORKER_TYPE_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    SDK_VERSION_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    NODE_CLASS_FIELD_NUMBER: _ClassVar[int]
    ICON_FIELD_NUMBER: _ClassVar[int]
    COLOR_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    INPUT_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    FUNCTIONS_FIELD_NUMBER: _ClassVar[int]
    worker_type: str
    version: str
    sdk_version: str
    name: str
    description: str
    node_class: str
    icon: str
    color: str
    tags: _containers.RepeatedScalarFieldContainer[str]
    input_schema: _struct_pb2.Struct
    output_schema: _struct_pb2.Struct
    functions: _containers.RepeatedCompositeFieldContainer[WorkerFunctionDef]
    def __init__(self, worker_type: _Optional[str] = ..., version: _Optional[str] = ..., sdk_version: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., node_class: _Optional[str] = ..., icon: _Optional[str] = ..., color: _Optional[str] = ..., tags: _Optional[_Iterable[str]] = ..., input_schema: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., output_schema: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., functions: _Optional[_Iterable[_Union[WorkerFunctionDef, _Mapping]]] = ...) -> None: ...

class WorkerFunctionDef(_message.Message):
    __slots__ = ("name", "description", "input_schema", "output_schema")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    INPUT_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    name: str
    description: str
    input_schema: _struct_pb2.Struct
    output_schema: _struct_pb2.Struct
    def __init__(self, name: _Optional[str] = ..., description: _Optional[str] = ..., input_schema: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., output_schema: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class InvokeFunctionRequest(_message.Message):
    __slots__ = ("function_name", "params")
    FUNCTION_NAME_FIELD_NUMBER: _ClassVar[int]
    PARAMS_FIELD_NUMBER: _ClassVar[int]
    function_name: str
    params: _struct_pb2.Struct
    def __init__(self, function_name: _Optional[str] = ..., params: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class InvokeFunctionResponse(_message.Message):
    __slots__ = ("status", "result", "error")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    status: str
    result: _struct_pb2.Struct
    error: str
    def __init__(self, status: _Optional[str] = ..., result: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., error: _Optional[str] = ...) -> None: ...

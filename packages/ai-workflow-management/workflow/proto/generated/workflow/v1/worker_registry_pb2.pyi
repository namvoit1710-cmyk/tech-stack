from google.protobuf import struct_pb2 as _struct_pb2
from workflow.v1 import worker_execution_pb2 as _worker_execution_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Capability(_message.Message):
    __slots__ = ("domain", "action")
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    domain: str
    action: str
    def __init__(self, domain: _Optional[str] = ..., action: _Optional[str] = ...) -> None: ...

class RegisterWorkerRequest(_message.Message):
    __slots__ = ("worker_type", "version", "endpoint", "sdk_version", "name", "description", "node_class", "icon", "color", "tags", "capabilities", "input_schema", "output_schema", "ports", "functions", "spec_version", "kind")
    WORKER_TYPE_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    ENDPOINT_FIELD_NUMBER: _ClassVar[int]
    SDK_VERSION_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    NODE_CLASS_FIELD_NUMBER: _ClassVar[int]
    ICON_FIELD_NUMBER: _ClassVar[int]
    COLOR_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    INPUT_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    PORTS_FIELD_NUMBER: _ClassVar[int]
    FUNCTIONS_FIELD_NUMBER: _ClassVar[int]
    SPEC_VERSION_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    worker_type: str
    version: str
    endpoint: str
    sdk_version: str
    name: str
    description: str
    node_class: str
    icon: str
    color: str
    tags: _containers.RepeatedScalarFieldContainer[str]
    capabilities: _containers.RepeatedCompositeFieldContainer[Capability]
    input_schema: _struct_pb2.Struct
    output_schema: _struct_pb2.Struct
    ports: _struct_pb2.Struct
    functions: _containers.RepeatedCompositeFieldContainer[_worker_execution_pb2.WorkerFunctionDef]
    spec_version: str
    kind: str
    def __init__(self, worker_type: _Optional[str] = ..., version: _Optional[str] = ..., endpoint: _Optional[str] = ..., sdk_version: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., node_class: _Optional[str] = ..., icon: _Optional[str] = ..., color: _Optional[str] = ..., tags: _Optional[_Iterable[str]] = ..., capabilities: _Optional[_Iterable[_Union[Capability, _Mapping]]] = ..., input_schema: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., output_schema: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., ports: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., functions: _Optional[_Iterable[_Union[_worker_execution_pb2.WorkerFunctionDef, _Mapping]]] = ..., spec_version: _Optional[str] = ..., kind: _Optional[str] = ...) -> None: ...

class RegisterWorkerResponse(_message.Message):
    __slots__ = ("worker_id", "worker_type", "version", "endpoint_url", "health_status")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    WORKER_TYPE_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    ENDPOINT_URL_FIELD_NUMBER: _ClassVar[int]
    HEALTH_STATUS_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    worker_type: str
    version: str
    endpoint_url: str
    health_status: str
    def __init__(self, worker_id: _Optional[str] = ..., worker_type: _Optional[str] = ..., version: _Optional[str] = ..., endpoint_url: _Optional[str] = ..., health_status: _Optional[str] = ...) -> None: ...

class DeregisterWorkerRequest(_message.Message):
    __slots__ = ("worker_id",)
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    def __init__(self, worker_id: _Optional[str] = ...) -> None: ...

class DeregisterWorkerResponse(_message.Message):
    __slots__ = ("success",)
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    success: bool
    def __init__(self, success: bool = ...) -> None: ...

class HeartbeatRequest(_message.Message):
    __slots__ = ("worker_id", "status")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    status: str
    def __init__(self, worker_id: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class HeartbeatResponse(_message.Message):
    __slots__ = ("acknowledged", "re_register")
    ACKNOWLEDGED_FIELD_NUMBER: _ClassVar[int]
    RE_REGISTER_FIELD_NUMBER: _ClassVar[int]
    acknowledged: bool
    re_register: bool
    def __init__(self, acknowledged: bool = ..., re_register: bool = ...) -> None: ...

class ListWorkersRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListWorkersResponse(_message.Message):
    __slots__ = ("workers",)
    WORKERS_FIELD_NUMBER: _ClassVar[int]
    workers: _containers.RepeatedCompositeFieldContainer[WorkerDetail]
    def __init__(self, workers: _Optional[_Iterable[_Union[WorkerDetail, _Mapping]]] = ...) -> None: ...

class GetWorkerRequest(_message.Message):
    __slots__ = ("worker_id",)
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    def __init__(self, worker_id: _Optional[str] = ...) -> None: ...

class WorkerDetail(_message.Message):
    __slots__ = ("worker_id", "worker_type", "version", "endpoint", "name", "description", "node_class", "icon", "color", "tags", "capabilities", "health_status", "ports", "functions", "spec_version", "kind")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    WORKER_TYPE_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    ENDPOINT_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    NODE_CLASS_FIELD_NUMBER: _ClassVar[int]
    ICON_FIELD_NUMBER: _ClassVar[int]
    COLOR_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    HEALTH_STATUS_FIELD_NUMBER: _ClassVar[int]
    PORTS_FIELD_NUMBER: _ClassVar[int]
    FUNCTIONS_FIELD_NUMBER: _ClassVar[int]
    SPEC_VERSION_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    worker_type: str
    version: str
    endpoint: str
    name: str
    description: str
    node_class: str
    icon: str
    color: str
    tags: _containers.RepeatedScalarFieldContainer[str]
    capabilities: _containers.RepeatedCompositeFieldContainer[Capability]
    health_status: str
    ports: _struct_pb2.Struct
    functions: _containers.RepeatedCompositeFieldContainer[_worker_execution_pb2.WorkerFunctionDef]
    spec_version: str
    kind: str
    def __init__(self, worker_id: _Optional[str] = ..., worker_type: _Optional[str] = ..., version: _Optional[str] = ..., endpoint: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., node_class: _Optional[str] = ..., icon: _Optional[str] = ..., color: _Optional[str] = ..., tags: _Optional[_Iterable[str]] = ..., capabilities: _Optional[_Iterable[_Union[Capability, _Mapping]]] = ..., health_status: _Optional[str] = ..., ports: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., functions: _Optional[_Iterable[_Union[_worker_execution_pb2.WorkerFunctionDef, _Mapping]]] = ..., spec_version: _Optional[str] = ..., kind: _Optional[str] = ...) -> None: ...

class GetWorkerSchemaRequest(_message.Message):
    __slots__ = ("worker_id",)
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    def __init__(self, worker_id: _Optional[str] = ...) -> None: ...

class WorkerSchemaResponse(_message.Message):
    __slots__ = ("input_schema", "output_schema")
    INPUT_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    input_schema: _struct_pb2.Struct
    output_schema: _struct_pb2.Struct
    def __init__(self, input_schema: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., output_schema: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class DiscoverWorkersRequest(_message.Message):
    __slots__ = ("capability",)
    CAPABILITY_FIELD_NUMBER: _ClassVar[int]
    capability: str
    def __init__(self, capability: _Optional[str] = ...) -> None: ...

class DiscoverWorkersResponse(_message.Message):
    __slots__ = ("workers",)
    WORKERS_FIELD_NUMBER: _ClassVar[int]
    workers: _containers.RepeatedCompositeFieldContainer[WorkerDetail]
    def __init__(self, workers: _Optional[_Iterable[_Union[WorkerDetail, _Mapping]]] = ...) -> None: ...

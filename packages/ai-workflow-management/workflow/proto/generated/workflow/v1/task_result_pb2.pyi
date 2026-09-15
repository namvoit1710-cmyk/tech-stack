from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class TaskCompletedRequest(_message.Message):
    __slots__ = ("run_id", "task_id", "output_reference", "output_metadata")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_REFERENCE_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_METADATA_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    task_id: str
    output_reference: str
    output_metadata: _struct_pb2.Struct
    def __init__(self, run_id: _Optional[str] = ..., task_id: _Optional[str] = ..., output_reference: _Optional[str] = ..., output_metadata: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class TaskCompletedResponse(_message.Message):
    __slots__ = ("completed", "status")
    COMPLETED_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    completed: bool
    status: str
    def __init__(self, completed: bool = ..., status: _Optional[str] = ...) -> None: ...

class TaskFailedRequest(_message.Message):
    __slots__ = ("run_id", "task_id", "error_code", "error_message", "retryable")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    ERROR_CODE_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    task_id: str
    error_code: str
    error_message: str
    retryable: bool
    def __init__(self, run_id: _Optional[str] = ..., task_id: _Optional[str] = ..., error_code: _Optional[str] = ..., error_message: _Optional[str] = ..., retryable: bool = ...) -> None: ...

class TaskFailedResponse(_message.Message):
    __slots__ = ("accepted", "status")
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    accepted: bool
    status: str
    def __init__(self, accepted: bool = ..., status: _Optional[str] = ...) -> None: ...

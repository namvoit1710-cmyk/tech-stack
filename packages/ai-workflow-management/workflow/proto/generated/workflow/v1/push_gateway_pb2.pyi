from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class SendDataRequest(_message.Message):
    __slots__ = ("key", "data", "is_final")
    KEY_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    IS_FINAL_FIELD_NUMBER: _ClassVar[int]
    key: str
    data: str
    is_final: bool
    def __init__(self, key: _Optional[str] = ..., data: _Optional[str] = ..., is_final: bool = ...) -> None: ...

class SendDataResponse(_message.Message):
    __slots__ = ("success", "message", "chunk_index", "is_final")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    CHUNK_INDEX_FIELD_NUMBER: _ClassVar[int]
    IS_FINAL_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    chunk_index: int
    is_final: bool
    def __init__(self, success: bool = ..., message: _Optional[str] = ..., chunk_index: _Optional[int] = ..., is_final: bool = ...) -> None: ...

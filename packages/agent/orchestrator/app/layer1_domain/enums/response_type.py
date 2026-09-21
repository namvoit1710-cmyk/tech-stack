from enum import Enum


class ResponseType(str, Enum):
    TEXT = "text"
    MARKDOWN = "markdown"
    STREAM_CHUNK = "stream_chunk"
    STREAM_END = "stream_end"
    UI_COMPONENT = "ui_component"
    ERROR = "error"
    DISAMBIGUATION = "disambiguation"

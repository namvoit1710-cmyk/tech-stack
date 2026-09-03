from dataclasses import dataclass


@dataclass
class WriteOptions:
    content_type: str = "application/octet-stream"
    overwrite: bool = False

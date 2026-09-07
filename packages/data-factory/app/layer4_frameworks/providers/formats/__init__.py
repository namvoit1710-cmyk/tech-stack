"""Source-format reader strategies (Dependency-Injection registry).

Each format is an ``ISourceReader`` implementation registered in the
``SourceReaderRegistry``. Add a new format = add a class + register it; no
change to the storage provider or business logic. See
``source_reader_interface.py``.
"""
from .registry import SourceReaderRegistry, default_source_reader_registry

__all__ = ["SourceReaderRegistry", "default_source_reader_registry"]

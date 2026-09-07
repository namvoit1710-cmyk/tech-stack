import os
from typing import List

from app.layer2_application.interfaces.source_reader_interface import ISourceReader

from .csv_reader import CsvSourceReader
from .excel_reader import ExcelSourceReader
from .json_reader import JsonSourceReader
from .parquet_reader import ParquetSourceReader


class SourceReaderRegistry:
    """Maps a file-format key to its ``ISourceReader`` strategy.

    Injected into the storage provider so that read/write dispatch is a table
    lookup rather than an ``if/elif`` chain. Register a new reader to support a
    new format — nothing else changes.
    """

    def __init__(self, readers: List[ISourceReader]):
        self._by_format = {}
        for reader in readers:
            for fmt in reader.formats:
                self._by_format[self._normalize(fmt)] = reader

    @staticmethod
    def _normalize(fmt: str) -> str:
        return (fmt or "").lstrip(".").lower()

    def supports(self, fmt: str) -> bool:
        return self._normalize(fmt) in self._by_format

    def for_format(self, fmt: str) -> ISourceReader:
        reader = self._by_format.get(self._normalize(fmt))
        if reader is None:
            raise ValueError(
                f"Unsupported file format '{fmt}'. "
                f"Supported formats: {', '.join(self.supported_formats)}."
            )
        return reader

    def detect(self, path: str) -> str:
        """Best-effort format from a path's extension (normalized, no dot)."""
        return self._normalize(os.path.splitext(path)[1])

    @property
    def supported_formats(self) -> tuple:
        return tuple(sorted(self._by_format))


def default_source_reader_registry() -> SourceReaderRegistry:
    """The formats shipped today. Adding a format is a one-line change here
    plus one new reader class."""
    return SourceReaderRegistry(
        [
            CsvSourceReader(),
            JsonSourceReader(),
            ParquetSourceReader(),
            ExcelSourceReader(),
        ]
    )

from typing import Protocol, Dict, Any


class IODataParser(Protocol):
    """Protocol for safely applying OData queries to datasets"""
    def parse_and_format(self, dataset: Any, odata_query: str) -> Dict[str, Any]:
        ...
    """Protocol for safely applying OData queries to datasets"""
    def parse_and_format(self, dataset: Any, odata_query: str) -> Dict[str, Any]:
        ...

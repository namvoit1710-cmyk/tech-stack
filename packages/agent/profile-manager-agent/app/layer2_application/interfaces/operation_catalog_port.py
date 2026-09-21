from typing import Any, Dict, List, Protocol, runtime_checkable


@runtime_checkable
class IOperationCatalog(Protocol):
    """Supplies the supported operation descriptors used to build the planner prompt.

    Replaces the domain-level ``pkgutil``/``importlib`` registry (P4). The
    concrete catalog lives in layer4.
    """

    def list_operations(self) -> List[Dict[str, Any]]: ...

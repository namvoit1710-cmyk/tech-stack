from typing import Any, Dict, List

from app.layer4_frameworks.prompt.operations.user_query import OPERATION


class OperationCatalog:
    """Supplies the supported operation descriptors (implements IOperationCatalog).

    Replaces the domain-level ``pkgutil``/``importlib`` registry (P4) with an
    explicit, static list — no dynamic import, and the operation data is a
    layer4 prompt asset.
    """

    def list_operations(self) -> List[Dict[str, Any]]:
        return [OPERATION]

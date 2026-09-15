from typing import Any, Literal, Protocol
from collections.abc import Mapping, Sequence

ScopeType = Literal["run", "workflow", "variables", "input", "node", "artifacts", "credentials", "env"]


class IScopeProvider(Protocol):
    """Builds zero-or-more :class:`Scope` records for a given run + target node.

    Each provider owns one ``scope_type``. The use case sorts providers by
    ``order`` and concatenates their outputs into the response's ``scopes``
    list. Providers are independent — adding a new scope kind is one new
    provider plus a bootstrap registration.
    """

    scope_type: ScopeType
    order: int

    async def build(
        self,
        *,
        run: Any,
        target_node_id: str,
        target_workflow: Any,
        variables: Mapping[str, Any],
        upstream_node_ids: Sequence[str],
    ) -> Sequence[Any]: ...

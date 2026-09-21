from typing import List, Protocol, runtime_checkable

from app.layer1_domain.entities.execution_plan import PlanStep
from app.layer1_domain.entities.query_result import QueryResult


@runtime_checkable
class IUserQueryPort(Protocol):
    """Executes the ``user``-scoped plan steps against profile-management.

    Async so the use case can ``await`` real network I/O without blocking the
    event loop (fixes the old blocking-``requests``-in-async-node smell, P9).
    """

    async def query(self, steps: List[PlanStep], token: str | None) -> QueryResult: ...

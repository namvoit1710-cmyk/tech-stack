from dataclasses import dataclass
from typing import Optional


@dataclass
class ODataQuery:
    """OData query parameters (enterprise entity, no framework imports).

    Kept as a dataclass with defaulted fields so both ``ODataQuery()`` and
    ``ODataQuery(**step["odata_query"])`` keep working. Use :meth:`create` when
    the query originates from an untrusted source and must be validated.
    """

    filter: Optional[str] = None
    orderby: Optional[str] = None
    top: Optional[int] = None
    skip: Optional[int] = None
    select: Optional[str] = None
    count: Optional[bool] = None

    @classmethod
    def create(
        cls,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        select: Optional[str] = None,
        count: Optional[bool] = None,
    ) -> "ODataQuery":
        """Domain factory with validation (mirrors blueprint ``FileMetadata.create``)."""
        if top is not None and top < 0:
            raise ValueError("top must be >= 0")
        if skip is not None and skip < 0:
            raise ValueError("skip must be >= 0")
        if filter is not None and filter.strip() == "":
            raise ValueError("filter, when provided, must be non-empty")
        return cls(
            filter=filter,
            orderby=orderby,
            top=top,
            skip=skip,
            select=select,
            count=count,
        )

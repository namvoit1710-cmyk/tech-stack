"""Controller-local helpers for resolving use cases from the app container."""

from fastapi import HTTPException, Request, status


def resolve_usecase(request: Request, usecase_name: str):
    container = getattr(request.app.state, "container", None)
    if not isinstance(container, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Application container is not configured",
        )

    usecases = container.get("_usecases", {})
    usecase = usecases.get(usecase_name)
    if usecase is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Use case '{usecase_name}' is not configured",
        )

    return usecase
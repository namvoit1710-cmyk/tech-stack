import logging
from urllib.parse import urlencode, quote

import httpx

from app.layer1_domain.entities.execution_plan import PlanStep
from app.layer1_domain.entities.odata_query import ODataQuery
from app.layer1_domain.entities.query_result import QueryResult
from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter


class UserQueryRepository:
    """Async OData executor for the ``user`` resource (implements IUserQueryPort).

    Uses ``httpx.AsyncClient`` so it does not block the event loop inside the
    async graph node (P9). URL/param encoding and 422/5xx handling are preserved
    byte-for-byte from the old blocking ``UserTransformationExecutor`` (C6).
    """

    def __init__(self) -> None:
        settings = AppSettingsAdapter()
        self.base_url = f"{settings.PROFILE_MANAGEMENT_BASE_URL}"

    async def query(
        self, steps: list[PlanStep], token: str | None = None
    ) -> QueryResult:
        result = QueryResult()
        for step in steps:
            logging.info("step=execute_row_operation operation=%s", "user")
            result = await self._execute_single(step.odata_query, token)
        return result

    async def _execute_single(
        self, odata_query: ODataQuery, token: str | None = None
    ) -> QueryResult:
        """Execute a single OData query against the API and return a QueryResult."""
        # NOTE: no Content-Type on a body-less GET — some backends try to parse a
        # JSON body when it is present and 500 ("Failed to retrieve users").
        headers = {
            "accept": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        params = {
            "$filter": odata_query.filter,
            "$orderby": odata_query.orderby,
            "$top": odata_query.top,
            "$skip": odata_query.skip,
            "$select": odata_query.select,
            "$count": odata_query.count,
        }
        # Remove None or empty values
        filtered_params = {
            k: v for k, v in params.items() if v is not None and v != ""
        }

        # Encode (spaces → %20 instead of +)
        query_string = urlencode(filtered_params, quote_via=quote)
        # Construct URL dynamically (currently for /users resource)
        url = f"{self.base_url}/api/v1/users?{query_string}"

        logging.info("Calling URL: %s", url)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
        except httpx.RequestError as e:
            raise RuntimeError(f"Failed to call service: {str(e)}")

        # Handle client error 422
        if response.status_code == 422:
            try:
                error_data = response.json()
                raise ValueError(f"Invalid request payload: {error_data}")
            except ValueError:
                raise ValueError("Invalid JSON payload sent to the service")

        # Handle server errors
        if response.status_code >= 500:
            try:
                error_data = response.json()
                detail = error_data.get("detail", "Internal server error")
                raise RuntimeError(f"Service error: {detail}")
            except Exception:
                raise RuntimeError("Service internal error")

        response.raise_for_status()

        data = response.json()
        users = data.get("users", []) if isinstance(data, dict) else []
        error = data.get("error") if isinstance(data, dict) else None
        return QueryResult(users=users, odata_query=query_string, error=error)

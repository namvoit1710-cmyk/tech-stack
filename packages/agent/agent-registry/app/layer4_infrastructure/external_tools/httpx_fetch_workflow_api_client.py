"""HTTPX client for fetching workflow definitions from external Workflow Control Plane API."""

import httpx

from json import JSONDecodeError
from pydantic import ValidationError
from app.layer1_domain.exceptions import InvalidDataException, NotFoundException
from app.layer2_application.interfaces.fetch_workflow_api_client import IFetchWorkflowApiClient
from app.layer2_application.dtos.workflow_fetch_dto import WorkflowFetchDTO
from app.layer2_application.interfaces.fetch_workflow_client_setting_port import FetchWorkflowClientSettings
from app.layer3_presentation.schemas.workflow_schema import WorkflowFetchResponse
from app.layer4_infrastructure.external_tools.httpx_retry import HttpxRetryHelper

class HttpxFetchWorkflowApiClient(IFetchWorkflowApiClient):
    """HTTPX implementation of IFetchWorkflowApiClient.
    
    This client uses HTTPX to make asynchronous HTTP requests to the external Workflow Control Plane API.
    It translates the API responses into domain Workflow entities.
    """

    def __init__(self, settings: FetchWorkflowClientSettings):
        """Initialize the client with the base URL of the external API."""
        self.settings = settings
        self._client: httpx.AsyncClient | None = None
        self.fetch_workflow_with_retry = HttpxRetryHelper.wrap_with_exponential_retry(
            self._fetch_workflow_once,
            retry_count=settings.fetch_retry_count,
            multiplier=settings.fetch_backoff_seconds,
            min_wait=settings.fetch_min_backoff_seconds,
            max_wait=settings.fetch_max_backoff_seconds,
            retry_predicate=lambda e: HttpxRetryHelper.should_retry_httpx(settings.retryable_exceptions, e),
        )
        
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.settings.fetch_timeout_seconds)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        
    async def _fetch_workflow_once(self, workflow_id: str) -> WorkflowFetchDTO:
        """Fetch workflow data from the external API using HTTPX without retries."""
        
        url = self.settings.fetch_workflow_url.format(workflow_id=workflow_id)
        client = await self._get_client()
        response = await client.get(url)
        response.raise_for_status()

        payload = response.json()
        
        valid_payload = WorkflowFetchResponse(**payload)  # Validate response structure

        if valid_payload.data is None:
            raise InvalidDataException(f"Workflow API returned no data for workflow_id: {workflow_id}")

        return WorkflowFetchDTO.from_api_response(valid_payload.data.model_dump())

    async def fetch_workflow(self, workflow_id: str) -> WorkflowFetchDTO:
        """Fetch workflow data from the external API using HTTPX with retries."""
        try:
            return await self.fetch_workflow_with_retry(workflow_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise NotFoundException(f"Workflow with id {workflow_id} not found from external API")
            raise InvalidDataException(f"Failed to fetch workflow data with workflow_id {workflow_id}")
        except httpx.HTTPError as e:
            raise InvalidDataException(f"Failed to fetch workflow data with workflow_id {workflow_id}") from e
        except JSONDecodeError as e:
            raise InvalidDataException(f"Invalid JSON response while fetching workflow data with workflow_id {workflow_id}") from e
        except ValidationError as e:
            raise InvalidDataException(f"Invalid workflow data received from external API with workflow_id {workflow_id}") from e
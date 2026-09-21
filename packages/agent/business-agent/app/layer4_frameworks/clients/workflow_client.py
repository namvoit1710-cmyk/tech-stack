import httpx
from typing import Optional
from app.layer1_domain.entities.workflow_entity import Workflow, WorkflowRunInfoResponse
from app.layer2_application.interfaces.workflow_client_interface import IWorkflowClient
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer1_domain.entities.workflow_entity import WorkNodeStatus
from app.layer1_domain.entities.workflow_node import InputNodeResponse, ArtifactPayload, NodePatchResponse, TaskNodeOutputResponse
from dataclasses import asdict


class WorkflowClient(IWorkflowClient):
    def __init__(self, base_url: str, logger: ILogger) -> None:
        self._base_url = base_url.rstrip("/")
        self._logger = logger

    # def get_all_by_agent_id(self, agent_id: str) -> list[Workflow]:
    #     if agent_id == "1":
    #         return [
    #             Workflow(
    #                 id="workflow_001",
    #                 name="trigger workflow create_template",
    #                 description="A workflow that automates the creation of a template.",
    #             ),
    #             Workflow(
    #                 id="workflow_002",
    #                 name="update_template",
    #                 description="A workflow that automates the updating of existing templates with user requirements.",
    #             ),
    #         ]
    #     return None

    async def get_by_id(self, workflow_id: str) -> Optional[Workflow]:

        return Workflow(
            id="workflow_001",
            name="trigger workflow create_template",
            description="A workflow that automates the creation of a template.",
        )

    async def get_node_info(
        self, run_id: str, node_id: str
    ) -> Optional[WorkNodeStatus]:
        if not self._base_url:
            self._logger.info(
                f"Workflow API URL is not configured. Cannot fetch node info for {node_id}"
            )
            return None

        # SA-890: the old `/api/v1/workflows/{run}/nodes/{node}` endpoint 404s on the
        # control plane, so node_info was always None and the sub-user-chat planner had
        # no field schema (it then used field labels as keys, or refused). The live
        # endpoint is `/api/v1/runs/{run}/nodes/{node}/schema`, which returns the field
        # schema (key + label) — exactly the input_schema the planner needs.
        url = f"{self._base_url}/api/v1/runs/{run_id}/nodes/{node_id}/schema"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()

            return WorkNodeStatus.from_schema_response(payload, run_id, node_id)

        except Exception as exc:
            self._logger.info(f"Failed to fetch node info for {node_id}: {exc}")
            return None

    async def get_workflow_run(self, run_id: str) -> Optional[WorkflowRunInfoResponse]:
        if not self._base_url:
            self._logger.info(
                f"Workflow API URL is not configured. Cannot fetch workflow run info for {run_id}"
            )
            return None

        url = f"{self._base_url}/api/v1/runs/{run_id}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()

                data = response.json()
                return WorkflowRunInfoResponse.from_dict(data)

        except Exception as exc:
            self._logger.info(f"Failed to fetch workflow run info for {run_id}: {exc}")
            return None

    async def get_input_node(
        self,
        run_id: str,
        node_id: str,
    ) -> Optional[InputNodeResponse]:
        if not self._base_url:
            self._logger.info(
                f"Workflow API URL is not configured. Cannot fetch input node for {run_id}"
            )
            return None

        url = f"{self._base_url}/api/v1/runs/{run_id}/nodes/{node_id}/schema"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()

                return InputNodeResponse.from_dict(response.json())

        except Exception as exc:
            self._logger.info(f"Failed to fetch input node for {run_id}: {exc}")
            return None
        
    async def patch_node_data(
    self,
    run_id: str,
    node_id: str,
    payload: ArtifactPayload,
) -> Optional[NodePatchResponse]:

        if not self._base_url:
            self._logger.info("Base URL not configured")
            return None

        url = f"{self._base_url}/api/v1/runs/{run_id}/nodes/{node_id}/data"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:

                # ----------------------------
                # 1. GET current node (to fetch ETag)
                # ----------------------------
                get_resp = await client.get(url)
                get_resp.raise_for_status()

                etag = get_resp.headers.get("ETag")
                if not etag:
                    self._logger.info("Missing ETag from GET response")
                    return None

                # ----------------------------
                # 2. PATCH with If-Match header
                # ----------------------------
                patch_resp = await client.patch(
                    url,
                    json=asdict(payload),
                    headers={
                        "If-Match": etag
                    }
                )

                patch_resp.raise_for_status()

                return NodePatchResponse.from_dict(patch_resp.json())

        except httpx.HTTPStatusError as exc:
            self._logger.info(
                f"HTTP error patching node data: {exc.response.text}"
            )
            return None

        except Exception as exc:
            self._logger.info(f"Failed to patch node data: {exc}")
            return None
        
    async def submit_node_data(
        self,
        run_id: str,
        node_id: str,
    ) -> Optional[NodePatchResponse]:
        if not self._base_url:
            self._logger.info("Base URL not configured")
            return None

        url = f"{self._base_url}/api/v1/runs/{run_id}/nodes/{node_id}/submit"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url)
                resp.raise_for_status()
                return NodePatchResponse.from_dict(resp.json())

        except Exception as exc:
            self._logger.info(f"Failed to submit node data: {exc}")
            return None

    async def get_node_output_data(
    self,
    run_id: str,
    node_id: str,
) -> Optional[TaskNodeOutputResponse]:
        if not self._base_url:
            return None

        url = (
            f"{self._base_url}/api/v1/runs/"
            f"{run_id}/nodes/{node_id}/data?side=output"
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    url,
                    headers={"accept": "application/json"},
                )
                resp.raise_for_status()

                payload = resp.json()
                return TaskNodeOutputResponse.from_dict(payload)

        except Exception as exc:
            self._logger.info(f"Failed to get node output data: {exc}")
            return None
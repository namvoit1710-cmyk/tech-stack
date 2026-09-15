"""Persistence infrastructure factory."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.repositories.workflow_snapshot_repository_interface import IWorkflowSnapshotRepository
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.repositories.stage_history_repository_interface import IStageHistoryRepository
from app.layer2_application.repositories.node_definition_repository_interface import INodeDefinitionRepository
from app.layer2_application.repositories.webhook_repository_interface import IWebhookRepository
from app.layer2_application.repositories.gateway_app_repo_interface import (
    IGatewayAppRepo,
)
from app.layer2_application.repositories.gateway_function_repo_interface import (
    IGatewayFunctionRepo,
)
from app.layer2_application.repositories.environment_repo_interface import (
    IEnvironmentRepo,
)
from app.layer2_application.repositories.env_section_schema_repo_interface import (
    IEnvSectionSchemaRepo,
)
from app.layer2_application.repositories.workflow_environment_repo_interface import (
    IWorkflowEnvironmentRepo,
)
from app.layer2_application.repositories.workflow_section_repo_interface import (
    IWorkflowSectionRepo,
)
from app.layer2_application.repositories.section_membership_repo_interface import (
    ISectionMembershipRepo,
)
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepositorySet:
    workflow: IWorkflowRepository
    workflow_snapshot: IWorkflowSnapshotRepository
    run: IRunRepository
    task: ITaskRepository
    stage_history: IStageHistoryRepository
    node_definition: INodeDefinitionRepository
    webhook: IWebhookRepository
    gateway_app: IGatewayAppRepo
    gateway_function: IGatewayFunctionRepo
    environment: IEnvironmentRepo
    env_section_schema: IEnvSectionSchemaRepo
    workflow_environment: IWorkflowEnvironmentRepo
    # SA-2605: workflow→section binding + Section↔Environment membership (supersede
    # the SA-2450 per-flow env-subset model above).
    workflow_section: IWorkflowSectionRepo
    section_membership: ISectionMembershipRepo
    backend_name: str


def _build_memory_repos(settings: Any) -> RepositorySet:
    from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
    from app.layer4_frameworks.persistence.memory.memory_workflow_snapshot_repo import MemoryWorkflowSnapshotRepository
    from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
    from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
    from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import MemoryStageHistoryRepository
    from app.layer4_frameworks.persistence.memory.memory_node_definition_repo import MemoryNodeDefinitionRepository
    from app.layer4_frameworks.persistence.memory.memory_webhook_repo import MemoryWebhookRepository
    from app.layer4_frameworks.persistence.memory.memory_gateway_app_repo import (
        MemoryGatewayAppRepo,
    )
    from app.layer4_frameworks.persistence.memory.memory_gateway_function_repo import (
        MemoryGatewayFunctionRepo,
    )
    from app.layer4_frameworks.persistence.memory.memory_environment_repo import (
        MemoryEnvironmentRepo,
    )
    from app.layer4_frameworks.persistence.memory.memory_env_section_schema_repo import (
        MemoryEnvSectionSchemaRepo,
    )
    from app.layer4_frameworks.persistence.memory.memory_workflow_environment_repo import (
        MemoryWorkflowEnvironmentRepo,
    )
    from app.layer4_frameworks.persistence.memory.memory_workflow_section_repo import (
        MemoryWorkflowSectionRepo,
    )
    from app.layer4_frameworks.persistence.memory.memory_section_membership_repo import (
        MemorySectionMembershipRepo,
    )

    return RepositorySet(
        workflow=MemoryWorkflowRepository(),
        workflow_snapshot=MemoryWorkflowSnapshotRepository(),
        run=MemoryRunRepository(),
        task=MemoryTaskRepository(),
        stage_history=MemoryStageHistoryRepository(),
        node_definition=MemoryNodeDefinitionRepository(),
        webhook=MemoryWebhookRepository(),
        gateway_app=MemoryGatewayAppRepo(),
        gateway_function=MemoryGatewayFunctionRepo(),
        environment=MemoryEnvironmentRepo(),
        env_section_schema=MemoryEnvSectionSchemaRepo(),
        workflow_environment=MemoryWorkflowEnvironmentRepo(),
        workflow_section=MemoryWorkflowSectionRepo(),
        section_membership=MemorySectionMembershipRepo(),
        backend_name="memory",
    )


def _read_mode(name: str) -> str:
    """A ``*_READ_MODE`` setting — "context" (default) | "shadow" | "table".

    Read from settings here rather than threaded through every caller of
    ``_build_hana_repos``: the run repo is built from a ``db`` handle alone, and
    the alternative is a parameter that exists solely to carry a string.
    """
    try:
        from app.layer4_frameworks.config.app_config import settings
        return str(getattr(settings, name, "context") or "context")
    except Exception:
        return "context"


def _build_hana_repos(db: Any) -> RepositorySet:
    from app.layer4_frameworks.persistence.hana.hana_workflow_repo import HanaWorkflowRepository
    from app.layer4_frameworks.persistence.hana.hana_workflow_snapshot_repo import HanaWorkflowSnapshotRepository
    from app.layer4_frameworks.persistence.hana.hana_run_repo import HanaRunRepository
    from app.layer4_frameworks.persistence.hana.hana_task_repo import HanaTaskRepository
    from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import MemoryStageHistoryRepository
    from app.layer4_frameworks.persistence.hana.hana_node_definition_repo import HanaNodeDefinitionRepository
    from app.layer4_frameworks.persistence.memory.memory_webhook_repo import MemoryWebhookRepository
    from app.layer4_frameworks.persistence.hana.hana_environment_repo import (
        HanaEnvironmentRepo,
    )
    from app.layer4_frameworks.persistence.hana.hana_env_section_schema_repo import (
        HanaEnvSectionSchemaRepo,
    )
    from app.layer4_frameworks.persistence.hana.hana_workflow_environment_repo import (
        HanaWorkflowEnvironmentRepo,
    )
    from app.layer4_frameworks.persistence.hana.hana_workflow_section_repo import (
        HanaWorkflowSectionRepo,
    )
    from app.layer4_frameworks.persistence.hana.hana_section_membership_repo import (
        HanaSectionMembershipRepo,
    )
    from app.layer4_frameworks.persistence.hana.hana_gateway_app_repo import (
        HanaGatewayAppRepo,
    )
    from app.layer4_frameworks.persistence.hana.hana_gateway_function_repo import (
        HanaGatewayFunctionRepo,
    )
    from app.layer4_frameworks.persistence.hana.hana_loop_state_repo import (
        HanaLoopStateRepository,
    )
    from app.layer4_frameworks.persistence.hana.hana_run_node_state_repo import (
        HanaRunNodeStateRepository,
    )

    return RepositorySet(
        workflow=HanaWorkflowRepository(db),
        workflow_snapshot=HanaWorkflowSnapshotRepository(db),
        # The loop-state mirror rides the run repo because ``save`` is the only
        # place ``context.loop_state`` reaches the database. Same ``db``, so it
        # shares the connection manager and its executor.
        run=HanaRunRepository(
            db,
            loop_state_repo=HanaLoopStateRepository(db),
            loop_state_read_mode=_read_mode("LOOP_STATE_READ_MODE"),
            node_state_repo=HanaRunNodeStateRepository(db),
            node_state_read_mode=_read_mode("NODE_STATE_READ_MODE"),
        ),
        task=HanaTaskRepository(db),
        stage_history=MemoryStageHistoryRepository(),
        node_definition=HanaNodeDefinitionRepository(db),
        webhook=MemoryWebhookRepository(),
        gateway_app=HanaGatewayAppRepo(db),
        gateway_function=HanaGatewayFunctionRepo(db),
        # SA-2365: HANA-backed environments (table AIW_ENVIRONMENT, migration 060)
        # so environments persist across restarts/redeploys like every other
        # tenant aggregate.
        environment=HanaEnvironmentRepo(db),
        # SA-2471: HANA-backed section key schema (AIW_ENV_SECTION_SCHEMA,
        # migration 062).
        env_section_schema=HanaEnvSectionSchemaRepo(db),
        # SA-2450: HANA-backed workflow↔section binding (AIW_WORKFLOW_ENVIRONMENT,
        # migration 061).
        workflow_environment=HanaWorkflowEnvironmentRepo(db),
        # SA-2605: workflow→section (AIW_WORKFLOW_SECTION, migration 065) +
        # Section↔Environment membership (AIW_SECTION_ENVIRONMENT, migration 066).
        workflow_section=HanaWorkflowSectionRepo(db),
        section_membership=HanaSectionMembershipRepo(db),
        backend_name="hana",
    )


def _build_sqlite_repos(settings: Any) -> RepositorySet:
    from app.layer4_frameworks.orm.config.database_config import DatabaseSessionManager
    from app.layer4_frameworks.orm.entities.base_entity import Base
    from app.layer4_frameworks.repositories.workflow_repository import WorkflowRepository
    from app.layer4_frameworks.repositories.run_repository import RunRepository
    from app.layer4_frameworks.repositories.task_repository import TaskRepository
    from app.layer4_frameworks.repositories.stage_history_repository import StageHistoryRepository

    import asyncio

    db_url = getattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./app.db")
    manager = DatabaseSessionManager(db_url)

    # Create tables synchronously
    asyncio.get_event_loop().run_until_complete(manager.create_tables(Base))

    session_factory = manager.get_session_factory()

    from app.layer4_frameworks.persistence.memory.memory_node_definition_repo import MemoryNodeDefinitionRepository
    from app.layer4_frameworks.persistence.memory.memory_webhook_repo import MemoryWebhookRepository
    from app.layer4_frameworks.persistence.memory.memory_workflow_snapshot_repo import MemoryWorkflowSnapshotRepository
    from app.layer4_frameworks.persistence.memory.memory_gateway_app_repo import (
        MemoryGatewayAppRepo,
    )
    from app.layer4_frameworks.persistence.memory.memory_gateway_function_repo import (
        MemoryGatewayFunctionRepo,
    )
    from app.layer4_frameworks.persistence.memory.memory_environment_repo import (
        MemoryEnvironmentRepo,
    )
    from app.layer4_frameworks.persistence.memory.memory_env_section_schema_repo import (
        MemoryEnvSectionSchemaRepo,
    )
    from app.layer4_frameworks.persistence.memory.memory_workflow_environment_repo import (
        MemoryWorkflowEnvironmentRepo,
    )
    from app.layer4_frameworks.persistence.memory.memory_workflow_section_repo import (
        MemoryWorkflowSectionRepo,
    )
    from app.layer4_frameworks.persistence.memory.memory_section_membership_repo import (
        MemorySectionMembershipRepo,
    )

    return RepositorySet(
        workflow=WorkflowRepository(session_factory),
        workflow_snapshot=MemoryWorkflowSnapshotRepository(),
        run=RunRepository(session_factory),
        task=TaskRepository(session_factory),
        stage_history=StageHistoryRepository(session_factory),
        node_definition=MemoryNodeDefinitionRepository(),
        webhook=MemoryWebhookRepository(),
        gateway_app=MemoryGatewayAppRepo(),
        gateway_function=MemoryGatewayFunctionRepo(),
        environment=MemoryEnvironmentRepo(),
        env_section_schema=MemoryEnvSectionSchemaRepo(),
        workflow_environment=MemoryWorkflowEnvironmentRepo(),
        workflow_section=MemoryWorkflowSectionRepo(),
        section_membership=MemorySectionMembershipRepo(),
        backend_name="sqlite",
    )


_BACKEND_REGISTRY: dict[str, callable] = {
    "memory": lambda db, settings: _build_memory_repos(settings),
    "hana": lambda db, settings: _build_hana_repos(db),
    "sqlite": lambda db, settings: _build_sqlite_repos(settings),
}


def create_repositories(settings: Any, db: Any=None) -> RepositorySet:
    if db is not None:
        infra_mode = getattr(settings, "INFRA_MODE", "mock").lower()
        backend_key = {"local": "hana", "sap": "hana"}.get(infra_mode, infra_mode)
    else:
        backend_key = "memory"

    builder = _BACKEND_REGISTRY.get(backend_key)
    if builder is None:
        supported = ", ".join(sorted(_BACKEND_REGISTRY))
        raise ValueError(f"Unknown persistence backend '{backend_key}'. Supported: {supported}")

    repos = builder(db, settings)
    logger.info("Persistence backend: %s", repos.backend_name)
    return repos

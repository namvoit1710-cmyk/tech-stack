"""HANA Agent Repository - Implementation of IAgentRepository port."""

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.layer1_domain.entities.agent import Agent, AgentKind, AgentStatus, ConfigType
from app.layer1_domain.exceptions import (
    AlreadyExistsException,
    InvalidDataException,
    NotFoundException,
)
from app.layer2_application.interfaces.agent_repository_port import IAgentRepository
from app.layer4_infrastructure.persistence.db.database import DatabaseFactory
from app.layer4_infrastructure.persistence.models.agent_model import (
    AgentModel,
)
from app.layer4_infrastructure.persistence.models.agent_tool_model import (
    AgentToolModel,
)
from app.layer4_infrastructure.persistence.models.agent_workflow_model import (
    AgentWorkflowModel,
)
from app.layer4_infrastructure.persistence.models.agent_pool_agent_model import (
    AgentPoolAgentModel,
)


class HANAAgentRepository(IAgentRepository):
    """HANA implementation of Agent Repository.
    
    Translates between domain Agent entities and AgentModel ORM models.
    """

    def __init__(self, db_factory: DatabaseFactory):
        """Initialize repository with database factory.
        
        Args:
            db_factory: Database factory for creating sessions
        """
        self.db_factory = db_factory
        
    def update_health_status(self, agent_id: str, is_alive: bool, last_health_check_at: datetime | None = None) -> None:
        """Update the health status of an agent.
        
        Args:
            agent_id: ID of agent to update
            is_alive: Liveness status to set
            last_health_check_at: Optional timestamp of the last health check
            
        Raises:
            NotFoundException: If agent doesn't exist
        """
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(AgentModel).where(AgentModel.id == agent_id, AgentModel.status != AgentStatus.DELETED.value)
            )
            orm_model = result.scalar_one_or_none()

            if not orm_model:
                raise NotFoundException("Agent", entity_id=agent_id)

            orm_model.is_alive = is_alive
            orm_model.last_health_check_at = last_health_check_at or datetime.now(timezone.utc)
            session.flush()
            
    def save(self, agent: Agent, tool_ids: list[str] | None = None, workflow_ids: list[str] | None = None) -> None:
        """Save agent immediately -> raise IntegrityError if agent with same name and version already exists.
        This approach avoid TOCTOU race condition in concurrent environment, but requires handling of IntegrityError exception at a higher level to convert it to a user-friendly error message.
        
        Args:
            agent: Agent entity to save
            tool_ids: Optional list of tool IDs to link to the agent
            workflow_ids: Optional list of workflow IDs to link to the agent
        Raises:
            AlreadyExistsException: If an agent with the same name and version already exists
            InvalidDataException: If saving fails due to invalid data
        """
        with self.db_factory.get_session() as session:
            try:
                orm_model = self._to_orm_model(agent)
                session.add(orm_model)
                session.flush()

                # Create tool relationships if provided
                if tool_ids:
                    for tool_id in dict.fromkeys(tool_ids):
                        relationship = AgentToolModel(
                            id=str(uuid4()),
                            agent_id=agent.id,
                            tool_id=tool_id,
                        )
                        session.add(relationship)

                # Create workflow relationships if provided
                if workflow_ids:
                    for workflow_id in dict.fromkeys(workflow_ids):
                        relationship = AgentWorkflowModel(
                            id=str(uuid4()),
                            agent_id=agent.id,
                            workflow_id=workflow_id,
                        )
                        session.add(relationship)

                session.flush()
            except IntegrityError as e:
                session.rollback()
                if 'unique constraint violated' in str(e).lower():
                    raise AlreadyExistsException(entity="Agent", entity_name=agent.name) from e
                else:
                    raise InvalidDataException(f"Failed to save agent due to integrity error: {str(e)}") from e

    def update(
        self,
        agent: Agent,
        tool_ids: list[str] | None = None,
        workflow_ids: list[str] | None = None,
    ) -> None:
        """Update an existing agent in the repository.
        
        Args:
            agent: Agent entity to update
            tool_ids: Optional full replacement list of tool IDs to link to the agent
            workflow_ids: Optional full replacement list of workflow IDs to link to the agent
            
        Raises:
            NotFoundException: If agent doesn't exist
        """
        with self.db_factory.get_session() as session:
            # Find existing agent
            result = session.execute(
                select(AgentModel).where(AgentModel.id == agent.id, AgentModel.status != AgentStatus.DELETED.value)
            )
            orm_model = result.scalar_one_or_none()

            if not orm_model:
                raise NotFoundException("Agent", entity_id=agent.id)

            # Update ORM model from domain entity
            self._update_orm_model(orm_model, agent)
            if tool_ids is not None:
                self._replace_tool_relationships(session, agent.id, tool_ids)

            if workflow_ids is not None:
                self._replace_workflow_relationships(session, agent.id, workflow_ids)

            session.flush()

    def soft_delete(self, agent_id: str) -> None:
        """Mark agent as deleted.
        
        Args:
            agent_id: ID of agent to delete
            
        Raises:
            NotFoundException: If agent doesn't exist
        """
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(AgentModel).where(AgentModel.id == agent_id, AgentModel.status != AgentStatus.DELETED.value)
            )
            orm_model = result.scalar_one_or_none()

            if not orm_model:
                raise NotFoundException("Agent", entity_id=agent_id)

            orm_model.status = AgentStatus.DELETED.value
            orm_model.updated_at = datetime.now(timezone.utc)

            # RBAC (T5): the agent is SOFT-deleted, so FK CASCADE won't fire — explicitly
            # clear its agent_pool_agents rows so no pool keeps a dangling link. (Replaces
            # the removed agent_users cleanup; that table is dropped in 20260716_02.)
            session.execute(delete(AgentPoolAgentModel).where(AgentPoolAgentModel.agent_id == agent_id))

            session.flush()

    def find_by_id(self, agent_id: str) -> Agent | None:
        """Find an agent by ID.
        
        Args:
            agent_id: ID of agent to find
            
        Returns:
            Agent entity if found, None otherwise
        """
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(AgentModel).where(AgentModel.id == agent_id, AgentModel.status != AgentStatus.DELETED.value))
            orm_model = result.scalar_one_or_none()

            if not orm_model:
                return None

            tool_ids = self._get_tool_ids_by_agent_ids(session, [agent_id])
            workflow_ids = self._get_workflow_ids_by_agent_ids(session, [agent_id])
            return self._to_domain_entity(
                orm_model,
                tools=tool_ids.get(agent_id, []),
                workflows=workflow_ids.get(agent_id, []),
            )
        
    def find_by_ids(self, agent_ids: list[str]) -> list[Agent]:
        """Find agents by a list of IDs.
        
        Args:
            agent_ids: List of agent IDs to find
            
        Returns:
            List of Agent entities matching the provided IDs
        """
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(AgentModel).where(AgentModel.id.in_(agent_ids), AgentModel.status != AgentStatus.DELETED.value)
            )
            orm_models = result.scalars().all()
            # Check if any requested IDs were not found and raise exception if so
            found_ids = {model.id for model in orm_models}
            missing_ids = set(agent_ids) - found_ids
            if missing_ids:
                raise NotFoundException("Agent", entity_id=", ".join(missing_ids))

            return self._to_domain_entities(session, orm_models)

    def find_by_name(self, name: str) -> Agent | None:
        """Find an agent by name.
        
        Args:
            name: Name of agent to find
            
        Returns:
            Agent entity if found, None otherwise
        """
        with self.db_factory.get_session() as session:
            orm_model = self._find_by_name_internal(session, name)
            if not orm_model:
                return None

            return self._to_domain_entities(session, [orm_model])[0]

    def find_by_name_and_version(self, name: str, version: str) -> Agent | None:
        """Find an agent by exact (name, version), including soft-deleted rows.

        Includes DELETED rows because uq_agents_name_version applies to every row, so this
        mirrors exactly what a save() would collide with.
        """
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(AgentModel).where(
                    AgentModel.name == name, AgentModel.version == version
                )
            )
            orm_model = result.scalars().first()
            if not orm_model:
                return None
            return self._to_domain_entities(session, [orm_model])[0]

    def find_by_criteria(
        self,
        agent_id: str | None = None,
        status: AgentStatus | None = None,
        kind: AgentKind | None = None,
        is_alive: bool | None = None,
        is_published: bool | None = None,
        user_email: str | None = None,
    ) -> list[Agent]:
        """Find agents by various criteria.

        Args:
            agent_id: Filter by agent ID
            status: Filter by agent status
            kind: Filter by agent kind
            is_alive: Filter by liveness status
            is_published: Filter by published status
            user_email: Filter by owner (user_email captured at registration)

        Returns:
            List of agents matching criteria
        """
        with self.db_factory.get_session() as session:
            query = select(AgentModel).where(AgentModel.status != AgentStatus.DELETED.value)

            # Apply filters
            if agent_id:
                query = query.where(AgentModel.id == agent_id)
            if status:
                query = query.where(AgentModel.status == status.value)
            if kind:
                query = query.where(AgentModel.kind == kind.value)
            if is_alive is not None:
                query = query.where(AgentModel.is_alive == is_alive)
            if is_published is not None:
                query = query.where(AgentModel.is_published == is_published)
            if user_email:
                query = query.where(AgentModel.user_email == user_email)

            result = session.execute(query)
            orm_models = result.scalars().all()

            # Convert to domain entities
            return self._to_domain_entities(session, orm_models)

    def find_all(self) -> list[Agent]:
        """Retrieve all agents from the repository.
        
        Returns:
            List of all agents
        """
        with self.db_factory.get_session() as session:
            result = session.execute(select(AgentModel).where(AgentModel.status != AgentStatus.DELETED.value))
            orm_models = result.scalars().all()

            return self._to_domain_entities(session, orm_models)
            
    def find_deleted_agents(self) -> list[str]:
        """Retrieve all deleted agents.

        Returns:
            List of deleted agent IDs
        """
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(AgentModel.id).where(AgentModel.status == AgentStatus.DELETED.value)
            )
            return [row[0] for row in result.fetchall()]

    # Private helper methods

    def _find_by_name_internal(
        self, session: Session, name: str
    ) -> AgentModel | None:
        """Internal helper to find agent by name within a session.
        
        Args:
            session: Database session
            name: Agent name
            
        Returns:
            AgentModel if found, None otherwise
        """
        result = session.execute(
            select(AgentModel).where(AgentModel.name == name, AgentModel.status != AgentStatus.DELETED.value)
        )
        return result.scalar_one_or_none()

    def _to_orm_model(self, agent: Agent) -> AgentModel:
        """Convert domain Agent entity to ORM model.
        
        Args:
            agent: Domain Agent entity
            
        Returns:
            AgentModel ORM instance
        """
        return AgentModel(
            id=agent.id,
            name=agent.name,
            kind=agent.kind.value,
            status=agent.status.value,
            is_published=agent.is_published,
            description=agent.description,
            healthcheck_endpoint=agent.healthcheck_endpoint,
            invoke_endpoint=agent.invoke_endpoint,
            is_alive=agent.is_alive,
            last_health_check_at=agent.last_health_check_at,
            version=agent.version,
            provider=agent.provider,
            model=agent.model,
            temperature=agent.temperature,
            max_tokens=agent.max_tokens,
            system_prompt=agent.system_prompt,
            config_type=agent.config_type.value,
            timeout_ms=agent.timeout_ms,
            max_concurrency=agent.max_concurrency,
            retry_count=agent.retry_count,
            streaming_supported=agent.streaming_supported,
            capabilities=json.dumps(agent.capabilities) if agent.capabilities else None,
            capability_summary=agent.capability_summary or None,
            attached_agent_ids=json.dumps(agent.agents) if agent.agents else None,
            knowledge_base=json.dumps(agent.knowledge_base) if agent.knowledge_base else None,
            custom_system_prompt=agent.custom_system_prompt,
            custom_instructions=json.dumps(agent.custom_instructions) if agent.custom_instructions else None,
            custom_restrictions=json.dumps(agent.custom_restrictions) if agent.custom_restrictions else None,
            blocked_topics=json.dumps(agent.blocked_topics) if agent.blocked_topics else None,
            blocked_keywords=json.dumps(agent.blocked_keywords) if agent.blocked_keywords else None,
            user_roles=json.dumps(agent.user_roles) if agent.user_roles else None,
            agent_metadata=json.dumps(agent.metadata) if agent.metadata else None,
            user_email=agent.user_email,
            tenant_id=agent.tenant_id,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )

    def _update_orm_model(self, orm_model: AgentModel, agent: Agent) -> None:
        """Update ORM model from domain entity.
        
        Args:
            orm_model: ORM model to update
            agent: Domain entity with new data
        """
        orm_model.name = agent.name
        orm_model.kind = agent.kind.value
        orm_model.status = agent.status.value
        orm_model.is_published = agent.is_published
        orm_model.description = agent.description
        orm_model.healthcheck_endpoint = agent.healthcheck_endpoint
        orm_model.invoke_endpoint = agent.invoke_endpoint
        orm_model.is_alive = agent.is_alive
        orm_model.last_health_check_at = agent.last_health_check_at
        orm_model.version = agent.version
        orm_model.provider = agent.provider
        orm_model.model = agent.model
        orm_model.temperature = agent.temperature
        orm_model.max_tokens = agent.max_tokens
        orm_model.system_prompt = agent.system_prompt
        orm_model.config_type = agent.config_type.value
        orm_model.timeout_ms = agent.timeout_ms
        orm_model.max_concurrency = agent.max_concurrency
        orm_model.retry_count = agent.retry_count
        orm_model.streaming_supported = agent.streaming_supported
        orm_model.capabilities = json.dumps(agent.capabilities) if agent.capabilities else None
        orm_model.capability_summary = agent.capability_summary or None
        orm_model.attached_agent_ids = json.dumps(agent.agents) if agent.agents else None
        orm_model.knowledge_base = json.dumps(agent.knowledge_base) if agent.knowledge_base else None
        orm_model.custom_system_prompt = agent.custom_system_prompt
        orm_model.custom_instructions = json.dumps(agent.custom_instructions) if agent.custom_instructions else None
        orm_model.custom_restrictions = json.dumps(agent.custom_restrictions) if agent.custom_restrictions else None
        orm_model.blocked_topics = json.dumps(agent.blocked_topics) if agent.blocked_topics else None
        orm_model.blocked_keywords = json.dumps(agent.blocked_keywords) if agent.blocked_keywords else None
        orm_model.agent_metadata = json.dumps(agent.metadata) if agent.metadata else None
        orm_model.updated_at = agent.updated_at

    def _to_domain_entities(self, session: Session, orm_models: list[AgentModel]) -> list[Agent]:
        """Convert ORM models to hydrated domain entities."""
        if not orm_models:
            return []

        agent_ids = [model.id for model in orm_models]
        tool_ids = self._get_tool_ids_by_agent_ids(session, agent_ids)
        workflow_ids = self._get_workflow_ids_by_agent_ids(session, agent_ids)

        return [
            self._to_domain_entity(
                model,
                tools=tool_ids.get(model.id, []),
                workflows=workflow_ids.get(model.id, []),
            )
            for model in orm_models
        ]

    def _get_tool_ids_by_agent_ids(self, session: Session, agent_ids: list[str]) -> dict[str, list[str]]:
        """Load tool IDs for the given agent IDs in one query."""
        if not agent_ids:
            return {}

        result = session.execute(
            select(AgentToolModel.agent_id, AgentToolModel.tool_id).where(AgentToolModel.agent_id.in_(agent_ids))
        )

        tool_ids_by_agent_id: dict[str, list[str]] = {agent_id: [] for agent_id in agent_ids}
        for agent_id, tool_id in result.all():
            tool_ids_by_agent_id.setdefault(agent_id, []).append(tool_id)

        return tool_ids_by_agent_id

    def _get_workflow_ids_by_agent_ids(self, session: Session, agent_ids: list[str]) -> dict[str, list[str]]:
        """Load workflow IDs for the given agent IDs in one query."""
        if not agent_ids:
            return {}

        result = session.execute(
            select(AgentWorkflowModel.agent_id, AgentWorkflowModel.workflow_id).where(
                AgentWorkflowModel.agent_id.in_(agent_ids)
            )
        )

        workflow_ids_by_agent_id: dict[str, list[str]] = {agent_id: [] for agent_id in agent_ids}
        for agent_id, workflow_id in result.all():
            workflow_ids_by_agent_id.setdefault(agent_id, []).append(workflow_id)

        return workflow_ids_by_agent_id

    def _replace_tool_relationships(self, session: Session, agent_id: str, tool_ids: list[str]) -> None:
        """Replace all tool relationships for an agent within the current transaction."""
        session.execute(delete(AgentToolModel).where(AgentToolModel.agent_id == agent_id))

        for tool_id in dict.fromkeys(tool_ids):
            session.add(
                AgentToolModel(
                    id=str(uuid4()),
                    agent_id=agent_id,
                    tool_id=tool_id,
                )
            )

    def _replace_workflow_relationships(self, session: Session, agent_id: str, workflow_ids: list[str]) -> None:
        """Replace all workflow relationships for an agent within the current transaction."""
        session.execute(delete(AgentWorkflowModel).where(AgentWorkflowModel.agent_id == agent_id))

        for workflow_id in dict.fromkeys(workflow_ids):
            session.add(
                AgentWorkflowModel(
                    id=str(uuid4()),
                    agent_id=agent_id,
                    workflow_id=workflow_id,
                )
            )

    def _to_domain_entity(
        self,
        orm_model: AgentModel,
        tools: list[str] | None = None,
        workflows: list[str] | None = None,
    ) -> Agent:
        """Convert ORM model to domain Agent entity.
        
        Args:
            orm_model: AgentModel ORM instance
            tools: Hydrated tool IDs linked to the agent
            workflows: Hydrated workflow IDs linked to the agent
            
        Returns:
            Domain Agent entity
        """
        return Agent(
            id=orm_model.id,
            name=orm_model.name,
            kind=AgentKind(orm_model.kind),
            status=AgentStatus(orm_model.status),
            is_published=orm_model.is_published,
            description=orm_model.description,
            healthcheck_endpoint=orm_model.healthcheck_endpoint,
            invoke_endpoint=orm_model.invoke_endpoint,
            is_alive=orm_model.is_alive,
            last_health_check_at=orm_model.last_health_check_at,
            version=orm_model.version,
            provider=orm_model.provider,
            model=orm_model.model,
            temperature=orm_model.temperature,
            max_tokens=orm_model.max_tokens,
            system_prompt=orm_model.system_prompt,
            config_type=ConfigType(orm_model.config_type),
            timeout_ms=orm_model.timeout_ms,
            max_concurrency=orm_model.max_concurrency,
            retry_count=orm_model.retry_count,
            streaming_supported=orm_model.streaming_supported,
            capabilities=json.loads(orm_model.capabilities) if orm_model.capabilities else [],
            capability_summary=orm_model.capability_summary or "",
            tools=tools or [],
            workflows=workflows or [],
            agents=json.loads(orm_model.attached_agent_ids) if orm_model.attached_agent_ids else [],
            knowledge_base=json.loads(orm_model.knowledge_base) if orm_model.knowledge_base else [],
            metadata=json.loads(orm_model.agent_metadata) if orm_model.agent_metadata else {},
            custom_system_prompt=orm_model.custom_system_prompt,
            custom_instructions=json.loads(orm_model.custom_instructions) if orm_model.custom_instructions else [],
            custom_restrictions=json.loads(orm_model.custom_restrictions) if orm_model.custom_restrictions else [],
            blocked_topics=json.loads(orm_model.blocked_topics) if orm_model.blocked_topics else [],
            blocked_keywords=json.loads(orm_model.blocked_keywords) if orm_model.blocked_keywords else [],
            user_roles=json.loads(orm_model.user_roles) if orm_model.user_roles else [],
            user_email=orm_model.user_email,
            tenant_id=orm_model.tenant_id,
            created_at=orm_model.created_at,
            updated_at=orm_model.updated_at,
        )

    # Junction table operations for many-to-many relationships

    def add_tool(self, agent_id: str, tool_id: str) -> None:
        """Link a tool to an agent."""
        with self.db_factory.get_session() as session:
            # Check if agent exists
            agent = session.execute(
                select(AgentModel).where(AgentModel.id == agent_id, AgentModel.status != AgentStatus.DELETED.value)
            ).scalar_one_or_none()
            if not agent:
                raise NotFoundException(entity_id=agent_id)

            # Check if relationship already exists
            existing = session.execute(
                select(AgentToolModel).where(
                    AgentToolModel.agent_id == agent_id,
                    AgentToolModel.tool_id == tool_id,
                )
            ).scalar_one_or_none()

            if existing:
                # Relationship already exists, silently succeed
                return

            # Create new relationship
            relationship = AgentToolModel(
                id=str(uuid4()),
                agent_id=agent_id,
                tool_id=tool_id,
            )
            session.add(relationship)
            session.flush()

    def remove_tool(self, agent_id: str, tool_id: str) -> None:
        """Unlink a tool from an agent."""
        with self.db_factory.get_session() as session:
            agent = session.execute(
                select(AgentModel).where(AgentModel.id == agent_id, AgentModel.status != AgentStatus.DELETED.value)
            ).scalar_one_or_none()
            
            # Check if agent exists
            if not agent:
                raise NotFoundException(entity_id=agent_id)

            relationship = session.execute(
                select(AgentToolModel).where(
                    AgentToolModel.agent_id == agent_id,
                    AgentToolModel.tool_id == tool_id,
                )
            ).scalar_one_or_none()

            if not relationship:
                # Relationship doesn't exist, silently succeed
                return

            session.delete(relationship)
            session.flush()

    def get_tools(self, agent_id: str) -> list[str]:
        """Get tool IDs linked to an agent."""
        with self.db_factory.get_session() as session:
            # Check if agent exists
            agent = session.execute(
                select(AgentModel).where(AgentModel.id == agent_id, AgentModel.status != AgentStatus.DELETED.value)
            ).scalar_one_or_none()
            if not agent:
                raise NotFoundException(entity_id=agent_id)

            # Get all tool IDs
            result = session.execute(
                select(AgentToolModel.tool_id).where(AgentToolModel.agent_id == agent_id)
            )
            return [row[0] for row in result.all()]

    def add_workflow(self, agent_id: str, workflow_id: str) -> None:
        """Link a workflow to an agent."""
        with self.db_factory.get_session() as session:
            # Check if agent exists
            agent = session.execute(
                select(AgentModel).where(AgentModel.id == agent_id, AgentModel.status != AgentStatus.DELETED.value)
            ).scalar_one_or_none()
            if not agent:
                raise NotFoundException(entity_id=agent_id)

            # Check if relationship already exists
            existing = session.execute(
                select(AgentWorkflowModel).where(
                    AgentWorkflowModel.agent_id == agent_id,
                    AgentWorkflowModel.workflow_id == workflow_id,
                )
            ).scalar_one_or_none()

            if existing:
                # Relationship already exists, silently succeed
                return

            # Create new relationship
            relationship = AgentWorkflowModel(
                id=str(uuid4()),
                agent_id=agent_id,
                workflow_id=workflow_id,
            )
            session.add(relationship)
            session.flush()

    def remove_workflow(self, agent_id: str, workflow_id: str) -> None:
        """Unlink a workflow from an agent."""
        with self.db_factory.get_session() as session:
            agent = session.execute(
                select(AgentModel).where(AgentModel.id == agent_id, AgentModel.status != AgentStatus.DELETED.value)
            ).scalar_one_or_none()
            
            # Check if agent exists
            if not agent:
                raise NotFoundException(entity_id=agent_id)

            relationship = session.execute(
                select(AgentWorkflowModel).where(
                    AgentWorkflowModel.agent_id == agent_id,
                    AgentWorkflowModel.workflow_id == workflow_id,
                )
            ).scalar_one_or_none()

            if not relationship:
                # Relationship doesn't exist, silently succeed
                return

            session.delete(relationship)
            session.flush()

    def get_workflows(self, agent_id: str) -> list[str]:
        """Get workflow IDs linked to an agent."""
        with self.db_factory.get_session() as session:
            # Check if agent exists
            agent = session.execute(
                select(AgentModel).where(AgentModel.id == agent_id, AgentModel.status != AgentStatus.DELETED.value)
            ).scalar_one_or_none()
            if not agent:
                raise NotFoundException(entity_id=agent_id)

            # Get all workflow IDs
            result = session.execute(
                select(AgentWorkflowModel.workflow_id).where(
                    AgentWorkflowModel.agent_id == agent_id
                )
            )
            return [row[0] for row in result.all()]

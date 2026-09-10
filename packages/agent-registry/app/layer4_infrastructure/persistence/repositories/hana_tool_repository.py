"""HANA Tool Repository - Implementation of IToolRepository port."""

from datetime import datetime, timezone
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.layer1_domain.entities.tool import Tool, ToolProtocol, ToolStatus
from app.layer1_domain.exceptions import (
    NotFoundException,
)
from app.layer2_application.interfaces.tool_repository_port import IToolRepository
from app.layer4_infrastructure.persistence.db.database import DatabaseFactory
from app.layer4_infrastructure.persistence.models.tool_model import ToolModel


class HANAToolRepository(IToolRepository):
    """HANA implementation of Tool Repository.

    Translates between domain Tool entities and ToolModel ORM models.
    """

    def __init__(self, db_factory: DatabaseFactory):
        """Initialize repository with database factory.

        Args:
            db_factory: Database factory for creating sessions
        """
        self.db_factory = db_factory

    def save(self, tool: Tool) -> None:
        """Save a new tool to the repository."""
        with self.db_factory.get_session() as session:
            # Convert domain entity to ORM model
            orm_model = self._to_orm_model(tool)
            session.add(orm_model)
            session.flush()

    def update(self, tool: Tool) -> None:
        """Update an existing tool in the repository."""
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(ToolModel).where(
                    ToolModel.id == tool.id,
                    ToolModel.status != ToolStatus.DELETED.value,
                )
            )
            orm_model = result.scalar_one_or_none()

            if not orm_model:
                raise NotFoundException("Tool", entity_id=tool.id)

            # Update ORM model
            self._update_orm_model(orm_model, tool)
            session.flush()

    def delete(self, tool_id: str) -> None:
        """Delete a tool from the repository."""
        with self.db_factory.get_session() as session:
            result = session.execute(select(ToolModel).where(ToolModel.id == tool_id))
            orm_model = result.scalar_one_or_none()

            if not orm_model:
                raise NotFoundException("Tool", entity_id=tool_id)

            session.delete(orm_model)
            session.flush()

    def find_by_id(self, tool_id: str) -> Tool | None:
        """Find a tool by ID."""
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(ToolModel).where(
                    ToolModel.id == tool_id,
                    ToolModel.status != ToolStatus.DELETED.value,
                )
            )
            orm_model = result.scalar_one_or_none()

            if not orm_model:
                return None

            return self._to_domain_entity(orm_model)
        
    def find_by_ids(self, tool_ids: list[str]) -> list[Tool]:
        """Find multiple tools by their IDs."""
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(ToolModel).where(
                    ToolModel.id.in_(tool_ids),
                    ToolModel.status != ToolStatus.DELETED.value,
                )
            )
            orm_models = result.scalars().all()
            # Check if any requested IDs were not found and raise exception if so
            found_ids = {model.id for model in orm_models}
            missing_ids = set(tool_ids) - found_ids
            if missing_ids:
                raise NotFoundException("Tool", entity_id=", ".join(missing_ids))
            return [self._to_domain_entity(model) for model in orm_models]

    def find_by_name(self, name: str) -> Tool | None:
        """Find a tool by name."""
        with self.db_factory.get_session() as session:
            return self._find_by_name_internal(session, name)

    def find_by_protocol(self, protocol: ToolProtocol) -> list[Tool]:
        """Find tools by protocol."""
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(ToolModel).where(
                    ToolModel.protocol == protocol.value,
                    ToolModel.status != ToolStatus.DELETED.value,
                )
            )
            orm_models = result.scalars().all()
            return [self._to_domain_entity(model) for model in orm_models]

    def find_by_status(self, status: ToolStatus) -> list[Tool]:
        """Find tools by status."""
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(ToolModel).where(
                    ToolModel.status == status.value,
                    ToolModel.status != ToolStatus.DELETED.value,
                )
            )
            orm_models = result.scalars().all()
            return [self._to_domain_entity(model) for model in orm_models]

    def find_all(self) -> list[Tool]:
        """Retrieve all tools from the repository."""
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(ToolModel).where(ToolModel.status != ToolStatus.DELETED.value)
            )
            orm_models = result.scalars().all()
            return [self._to_domain_entity(model) for model in orm_models]
        
    def soft_delete(self, tool_id: str) -> None:
        """Mark tool as deleted.
        
        Args:
            tool_id: ID of tool to delete
            
        Raises:
            NotFoundException: If tool doesn't exist
        """
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(ToolModel).where(ToolModel.id == tool_id, ToolModel.status != ToolStatus.DELETED.value)
            )
            orm_model = result.scalar_one_or_none()
    
            if not orm_model:
                raise NotFoundException("Tool", entity_id=tool_id)

            orm_model.status = ToolStatus.DELETED.value
            orm_model.updated_at = datetime.now(timezone.utc)
            
            session.flush()

    # Private helper methods

    def _find_by_name_internal(self, session: Session, name: str) -> Tool | None:
        """Internal method to find tool by name within a session."""
        result = session.execute(
            select(ToolModel).where(
                ToolModel.name == name,
                ToolModel.status != ToolStatus.DELETED.value,
            )
        )
        orm_model = result.scalar_one_or_none()

        if not orm_model:
            return None

        return self._to_domain_entity(orm_model)

    def _to_orm_model(self, tool: Tool) -> ToolModel:
        """Convert domain entity to ORM model."""
        return ToolModel(
            id=tool.id,
            name=tool.name,
            description=tool.description,
            protocol=tool.protocol.value,
            endpoint=tool.endpoint,
            parameters_schema=json.dumps(tool.parameters_schema)
            if tool.parameters_schema
            else None,
            response_schema=json.dumps(tool.response_schema)
            if tool.response_schema
            else None,
            auth_config=json.dumps(tool.auth_config) if tool.auth_config else None,
            version=tool.version,
            status=tool.status.value,
            entity_metadata=json.dumps(tool.metadata) if tool.metadata else None,
            created_at=tool.created_at,
            updated_at=tool.updated_at,
        )

    def _to_domain_entity(self, orm_model: ToolModel) -> Tool:
        """Convert ORM model to domain entity."""
        return Tool(
            id=orm_model.id,
            name=orm_model.name,
            description=orm_model.description,
            protocol=ToolProtocol(orm_model.protocol),
            endpoint=orm_model.endpoint,
            parameters_schema=json.loads(orm_model.parameters_schema)
            if orm_model.parameters_schema
            else {},
            response_schema=json.loads(orm_model.response_schema)
            if orm_model.response_schema
            else {},
            auth_config=json.loads(orm_model.auth_config) if orm_model.auth_config else {},
            version=orm_model.version,
            status=ToolStatus(orm_model.status),
            metadata=json.loads(orm_model.entity_metadata) if orm_model.entity_metadata else {},
            created_at=orm_model.created_at,
            updated_at=orm_model.updated_at,
        )

    def _update_orm_model(self, orm_model: ToolModel, tool: Tool) -> None:
        """Update ORM model from domain entity."""
        orm_model.name = tool.name
        orm_model.description = tool.description
        orm_model.protocol = tool.protocol.value
        orm_model.endpoint = tool.endpoint
        orm_model.parameters_schema = (
            json.dumps(tool.parameters_schema) if tool.parameters_schema else None
        )
        orm_model.response_schema = (
            json.dumps(tool.response_schema) if tool.response_schema else None
        )
        orm_model.auth_config = json.dumps(tool.auth_config) if tool.auth_config else None
        orm_model.version = tool.version
        orm_model.status = tool.status.value
        orm_model.entity_metadata = json.dumps(tool.metadata) if tool.metadata else None
        orm_model.updated_at = tool.updated_at

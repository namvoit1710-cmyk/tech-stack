"""HANA Workflow Repository - Implementation of IWorkflowRepository port."""

import json

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.exceptions import (
    NotFoundException,
    InvalidDataException,
)
from app.layer2_application.interfaces.workflow_repository_port import IWorkflowRepository
from app.layer4_infrastructure.persistence.db.database import DatabaseFactory
from app.layer4_infrastructure.persistence.models.workflow_model import WorkflowModel


class HANAWorkflowRepository(IWorkflowRepository):
    """HANA implementation of Workflow Repository.

    Translates between domain Workflow entities and WorkflowModel ORM models.
    """

    def __init__(self, db_factory: DatabaseFactory):
        """Initialize repository with database factory.

        Args:
            db_factory: Database factory for creating sessions
        """
        self.db_factory = db_factory

    def save(self, workflow: Workflow) -> Workflow:
        """Save a new workflow to the repository."""
        with self.db_factory.get_session() as session:
            # Convert domain entity to ORM model
            orm_model = self._to_orm_model(workflow)
            session.add(orm_model)
            session.flush()
            return workflow
        
    def upsert(self, workflow: Workflow) -> Workflow:
        """Upsert a workflow in the repository."""
        with self.db_factory.get_session() as session:
            stmt = text(f"""
                UPSERT {WorkflowModel.__tablename__} (
                    ID,
                    NAME,
                    VERSION,
                    DESCRIPTION,
                    STATUS,
                    MAIN_FLOW,
                    METADATA,
                    INPUT_SCHEMA,
                    OUTPUT_SCHEMA,
                    UPDATED_AT,
                    CREATED_AT
                )
                VALUES (
                    :id,
                    :name,
                    :version,
                    :description,
                    :status,
                    :main_flow,
                    :metadata,
                    :input_schema,
                    :output_schema,
                    :updated_at,
                    :created_at
                )
                WITH PRIMARY KEY
                """)
            try:
                session.execute(
                    stmt,
                    {
                        "id": workflow.id,
                        "name": workflow.name,
                        "version": workflow.version,
                        "description": workflow.description,
                        "status": workflow.status,
                        "main_flow": workflow.main_flow,
                        "metadata": json.dumps(workflow.metadata),
                        "input_schema": json.dumps(workflow.input_schema),
                        "output_schema": json.dumps(workflow.output_schema),
                        "updated_at": workflow.updated_at,
                        "created_at": workflow.created_at,
                    },
                )
                
                session.flush()
                return workflow
            except Exception as e:
                raise InvalidDataException(f"Failed to upsert workflow {workflow.id} into repository: {str(e)}") from e

    def update(self, workflow: Workflow) -> Workflow:
        """Update an existing workflow in the repository."""
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(WorkflowModel).where(WorkflowModel.id == workflow.id)
            )
            orm_model = result.scalar_one_or_none()

            if not orm_model:
                raise NotFoundException("Workflow", entity_id=workflow.id)

            # Update ORM model
            self._update_orm_model(orm_model, workflow)
            session.flush()
            return workflow
        
    def delete(self, workflow_id: str) -> None:
        """Delete a workflow from the repository."""
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(WorkflowModel).where(WorkflowModel.id == workflow_id)
            )
            orm_model = result.scalar_one_or_none()

            if not orm_model:
                raise NotFoundException("Workflow", entity_id=workflow_id)

            session.delete(orm_model)
            session.flush()

    def find_by_id(self, workflow_id: str) -> Workflow | None:
        """Find a workflow by ID."""
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(WorkflowModel).where(WorkflowModel.id == workflow_id)
            )
            orm_model = result.scalar_one_or_none()

            if not orm_model:
                return None

            return self._to_domain_entity(orm_model)
        
    def find_by_ids(self, workflow_ids: list[str]) -> list[Workflow]:
        """Find multiple workflows by their IDs."""
        with self.db_factory.get_session() as session:
            result = session.execute(select(WorkflowModel).where(WorkflowModel.id.in_(workflow_ids)))
            orm_models = result.scalars().all()
            # Check if any requested IDs were not found and raise exception if so
            found_ids = {model.id for model in orm_models}
            missing_ids = set(workflow_ids) - found_ids
            if missing_ids:
                raise NotFoundException("Workflow", entity_id=", ".join(missing_ids))
            return [self._to_domain_entity(model) for model in orm_models]

    def find_by_name(self, name: str) -> Workflow | None:
        """Find a workflow by name."""
        with self.db_factory.get_session() as session:
            return self._find_by_name_internal(session, name)

    def find_by_version(self, version: str) -> list[Workflow]:
        """Find workflows by version."""
        with self.db_factory.get_session() as session:
            result = session.execute(
                select(WorkflowModel).where(WorkflowModel.version == version)
            )
            orm_models = result.scalars().all()
            return [self._to_domain_entity(model) for model in orm_models]

    def find_all(self) -> list[Workflow]:
        """Retrieve all workflows from the repository."""
        with self.db_factory.get_session() as session:
            result = session.execute(select(WorkflowModel))
            orm_models = result.scalars().all()
            return [self._to_domain_entity(model) for model in orm_models]

    # Private helper methods

    def _find_by_name_internal(self, session: Session, name: str) -> Workflow | None:
        """Internal method to find workflow by name within a session."""
        result = session.execute(select(WorkflowModel).where(WorkflowModel.name == name))
        orm_model = result.scalar_one_or_none()

        if not orm_model:
            return None

        return self._to_domain_entity(orm_model)

    def _to_orm_model(self, workflow: Workflow) -> WorkflowModel:
        """Convert domain entity to ORM model."""
        return WorkflowModel(
            id=workflow.id,
            name=workflow.name,
            description=workflow.description,
            version=workflow.version,
            status=workflow.status,
            main_flow=workflow.main_flow,
            workflow_metadata=json.dumps(workflow.metadata) if workflow.metadata else None,
            input_schema=json.dumps(workflow.input_schema)
            if workflow.input_schema is not None
            else None,
            output_schema=json.dumps(workflow.output_schema)
            if workflow.output_schema is not None
            else None,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
        )

    def _to_domain_entity(self, orm_model: WorkflowModel) -> Workflow:
        """Convert ORM model to domain entity."""
        return Workflow(
            id=orm_model.id,
            name=orm_model.name,
            description=orm_model.description,
            version=orm_model.version,
            status=orm_model.status,
            main_flow=orm_model.main_flow,
            metadata=json.loads(orm_model.workflow_metadata)
            if orm_model.workflow_metadata is not None
            else {},
            input_schema=json.loads(orm_model.input_schema)
            if orm_model.input_schema is not None
            else None,
            output_schema=json.loads(orm_model.output_schema)
            if orm_model.output_schema is not None
            else None,
            created_at=orm_model.created_at,
            updated_at=orm_model.updated_at,
        )

    def _update_orm_model(self, orm_model: WorkflowModel, workflow: Workflow) -> None:
        """Update ORM model from domain entity."""
        orm_model.name = workflow.name
        orm_model.description = workflow.description
        orm_model.version = workflow.version
        orm_model.status = workflow.status
        orm_model.main_flow = workflow.main_flow
        orm_model.workflow_metadata = (
            json.dumps(workflow.metadata) if workflow.metadata else None
        )
        orm_model.input_schema = (
            json.dumps(workflow.input_schema)
            if workflow.input_schema is not None
            else None
        )
        orm_model.output_schema = (
            json.dumps(workflow.output_schema)
            if workflow.output_schema is not None
            else None
        )
        orm_model.updated_at = workflow.updated_at

"""Unit tests for HANA workflow repository mapping behavior."""

from contextlib import contextmanager
from unittest.mock import Mock

from app.layer1_domain.entities.workflow import Workflow
from app.layer4_infrastructure.persistence.models.workflow_model import WorkflowModel
from app.layer4_infrastructure.persistence.repositories.hana_workflow_repository import (
    HANAWorkflowRepository,
)


@contextmanager
def _session_scope(session):
    yield session


def _build_repository(session=None):
    db_factory = Mock()
    db_factory.get_session.return_value = _session_scope(session or Mock())
    return HANAWorkflowRepository(db_factory)


class TestWorkflowRepositoryMapping:
    """Test workflow repository mapping helpers."""

    def test_to_orm_model_serializes_metadata(self, sample_workflow_data):
        repository = _build_repository()
        workflow_data = dict(sample_workflow_data)
        workflow_data["metadata"] = {"source": "api", "priority": 1}
        workflow = Workflow(**workflow_data)

        orm_model = repository._to_orm_model(workflow)

        assert orm_model.workflow_metadata == '{"source": "api", "priority": 1}'

    def test_to_domain_entity_deserializes_metadata(self):
        repository = _build_repository()
        orm_model = WorkflowModel(
            id="workflow-1",
            name="workflow-name",
            description="workflow description",
            version="1.0.0",
            status="active",
            main_flow=False,
            workflow_metadata='{"source": "api", "priority": 1}',
            input_schema='[{"name": "input"}]',
            output_schema='[{"name": "output"}]',
        )

        workflow = repository._to_domain_entity(orm_model)

        assert workflow.metadata == {"source": "api", "priority": 1}
        assert workflow.input_schema == [{"name": "input"}]
        assert workflow.output_schema == [{"name": "output"}]

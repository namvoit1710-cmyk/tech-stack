"""Unit tests for Workflow entity."""

import pytest
from datetime import datetime
from uuid6 import uuid7

from app.layer1_domain.entities.workflow import Workflow



class TestWorkflowEntity:
    """Test Workflow entity business logic."""

    def test_workflow_creation(self, sample_workflow_data):
        """Test creating a basic workflow."""
        workflow = Workflow(**sample_workflow_data)
        assert workflow.name == "test-workflow"
        assert workflow.version == "1.0.0"
        assert workflow.status == "active"
        assert workflow.main_flow is False

    def test_create_workflow_with_valid_data(self):
        """Test creating workflow with valid data using factory method."""
        workflow_id = str(uuid7())
        workflow = Workflow.create(
            id=workflow_id,
            name="test-workflow",
            description="A test workflow",
            version="1.0.0",
            status="active",
        )
        
        assert workflow.id == workflow_id
        assert workflow.name == "test-workflow"
        assert workflow.version == "1.0.0"  # default
        assert workflow.status == "active"  # default
        assert workflow.main_flow is False

    def test_create_workflow_with_main_flow(self):
        """Test creating workflow with main_flow enabled."""
        workflow = Workflow.create(
            id=str(uuid7()),
            name="main-workflow",
            description="Main workflow",
            version="1.0.0",
            status="active",
            main_flow=True,
        )

        assert workflow.main_flow is True

    def test_create_workflow_with_custom_version(self):
        """Test creating workflow with custom version."""
        workflow = Workflow.create(
            id=str(uuid7()),
            name="versioned-workflow",
            description="Workflow with custom version",
            version="2.5.3",
            status="active",
        )
        assert workflow.version == "2.5.3"

    def test_create_workflow_with_different_statuses(self):
        """Test creating workflows with different statuses."""
        for status in ["active", "inactive", "deprecated", "custom-status"]:
            workflow = Workflow.create(
                id=str(uuid7()),
                name=f"{status}-workflow",
                description=f"Workflow with {status} status",
                version="1.0.0",
                status=status,
            )
            assert workflow.status == status

    def test_create_workflow_with_empty_name(self):
        """Test creating workflow with empty name."""
        workflow = Workflow.create(
            id=str(uuid7()),
            name="",
            description="Test workflow",
            version="1.0.0",
            status="active",
        )
        assert workflow.name == ""

    def test_create_workflow_with_whitespace_name(self):
        """Test creating workflow with whitespace name produces empty string."""
        workflow = Workflow.create(
            id=str(uuid7()),
            name="   ",
            description="Test workflow",
            version="1.0.0",
            status="active",
        )
        assert workflow.name == ""

    def test_create_workflow_with_long_name(self):
        """Test creating workflow with name > 255 chars."""
        workflow = Workflow.create(
            id=str(uuid7()),
            name="w" * 256,
            description="Test workflow",
            version="1.0.0",
            status="active",
        )
        assert workflow.name == "w" * 256

    def test_create_workflow_with_empty_version(self):
        """Test creating workflow with empty version."""
        workflow = Workflow.create(
            id=str(uuid7()),
            name="test-workflow",
            description="Test workflow",
            version="",
            status="active",
        )
        assert workflow.version == ""

    def test_create_workflow_with_whitespace_version(self):
        """Test creating workflow with whitespace version produces empty string."""
        workflow = Workflow.create(
            id=str(uuid7()),
            name="test-workflow",
            description="Test workflow",
            version="   ",
            status="active",
        )
        assert workflow.version == ""

    def test_create_workflow_with_empty_status(self):
        """Test creating workflow with empty status."""
        workflow = Workflow.create(
            id=str(uuid7()),
            name="test-workflow",
            description="Test workflow",
            version="1.0.0",
            status="",
        )
        assert workflow.status == ""

    def test_create_workflow_strips_name_whitespace(self):
        """Test that workflow name is stripped of whitespace."""
        workflow = Workflow.create(
            id=str(uuid7()),
            name="  test-workflow  ",
            description="Test",
            version="1.0.0",
            status="active",
        )
        assert workflow.name == "test-workflow"

    def test_create_workflow_strips_version_whitespace(self):
        """Test that workflow version is stripped of whitespace."""
        workflow = Workflow.create(
            id=str(uuid7()),
            name="test-workflow",
            description="Test",
            version="  1.0.0  ",
            status="active",
        )
        assert workflow.version == "1.0.0"

    def test_create_workflow_strips_status_whitespace(self):
        """Test that workflow status is stripped of whitespace."""
        workflow = Workflow.create(
            id=str(uuid7()),
            name="test-workflow",
            description="Test",
            version="1.0.0",
            status="  custom-status  ",
        )
        assert workflow.status == "custom-status"

    def test_workflow_timestamps_are_set(self):
        """Test that created_at and updated_at are automatically set."""
        workflow = Workflow.create(
            id=str(uuid7()),
            name="test-workflow",
            description="Test",
            version="1.0.0",
            status="active",
        )
        assert workflow.created_at is not None
        assert workflow.updated_at is not None
        assert workflow.created_at <= workflow.updated_at

    def test_update_workflow_name(self, sample_workflow_data):
        """Test updating workflow name."""
        workflow = Workflow(**sample_workflow_data)
        original_updated_at = workflow.updated_at
        
        workflow.update(name="updated-workflow")
        
        assert workflow.name == "updated-workflow"
        # Note: The update method should update the updated_at timestamp

    def test_update_workflow_description(self, sample_workflow_data):
        """Test updating workflow description."""
        workflow = Workflow(**sample_workflow_data)
        
        workflow.update(description="Updated description")
        
        assert workflow.description == "Updated description"

    def test_update_workflow_version(self, sample_workflow_data):
        """Test updating workflow version."""
        workflow = Workflow(**sample_workflow_data)
        
        workflow.update(version="2.0.0")
        
        assert workflow.version == "2.0.0"

    def test_update_workflow_status(self, sample_workflow_data):
        """Test updating workflow status."""
        workflow = Workflow(**sample_workflow_data)
        
        workflow.update(status="deprecated")
        
        assert workflow.status == "deprecated"

    def test_update_workflow_main_flow(self, sample_workflow_data):
        """Test updating workflow main_flow flag."""
        workflow = Workflow(**sample_workflow_data)

        workflow.update(main_flow=True)

        assert workflow.main_flow is True

    def test_update_workflow_multiple_fields(self, sample_workflow_data):
        """Test updating multiple workflow fields at once."""
        workflow = Workflow(**sample_workflow_data)
        
        workflow.update(
            name="updated-workflow",
            description="Updated description",
            version="2.0.0",
            status="inactive",
        )
        
        assert workflow.name == "updated-workflow"
        assert workflow.description == "Updated description"
        assert workflow.version == "2.0.0"
        assert workflow.status == "inactive"

    def test_update_workflow_with_empty_status(self, sample_workflow_data):
        """Test updating workflow with empty status produces empty string."""
        workflow = Workflow(**sample_workflow_data)

        workflow.update(status="   ")

        assert workflow.status == ""

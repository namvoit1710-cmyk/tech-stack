import pytest
from app.layer4_frameworks.orm.entities.rule_entity import RuleSetOrmEntity
from app.layer4_frameworks.orm.base_entity import BaseOrmEntity
from app.layer4_frameworks.orm.base_audit_entity import BaseAuditOrmEntity

@pytest.mark.unit
class TestOrmEntities:
    def test_rule_set_orm_entity_creation(self):
        """Test RuleSetOrmEntity creation"""
        entity = RuleSetOrmEntity(
            name="Test Rule Set",
            description="Test description",
            status="active",
            rules=[{"type": "required", "params": {}}]
        )

        assert entity.name == "Test Rule Set"
        assert entity.description == "Test description"
        assert entity.status == "active"
        assert entity.rules == [{"type": "required", "params": {}}]
        assert hasattr(entity, 'id')  # From BaseOrmEntity
        assert hasattr(entity, 'created_at')  # From BaseAuditOrmEntity
        assert hasattr(entity, 'updated_at')  # From BaseAuditOrmEntity

    def test_rule_set_orm_entity_table_name(self):
        """Test that RuleSetOrmEntity has correct table name"""
        assert RuleSetOrmEntity.__tablename__ == "rule_sets"

@pytest.mark.unit
class TestBaseOrmEntity:
    def test_base_orm_entity_has_id(self):
        """Test that BaseOrmEntity has id field"""
        # Check that RuleSetOrmEntity (inheriting from BaseOrmEntity) has id
        entity = RuleSetOrmEntity(name="Test")
        assert hasattr(entity, 'id')

@pytest.mark.unit
class TestBaseAuditOrmEntity:
    def test_base_audit_orm_entity_has_timestamps(self):
        """Test that BaseAuditOrmEntity has timestamp fields"""
        # Check that RuleSetOrmEntity (inheriting from BaseAuditOrmEntity) has timestamps
        entity = RuleSetOrmEntity(name="Test")
        assert hasattr(entity, 'created_at')
        assert hasattr(entity, 'updated_at')
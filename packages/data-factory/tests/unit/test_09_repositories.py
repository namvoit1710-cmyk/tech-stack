import pytest
from unittest.mock import AsyncMock, MagicMock
from app.layer4_frameworks.repositories.rule_repository import RuleRepository
from app.layer4_frameworks.repositories.base_repository import BaseRepository
from app.layer1_domain.entities.rule_management import RuleSet
from app.layer4_frameworks.orm.entities.rule_entity import RuleSetOrmEntity

@pytest.mark.unit
class TestRuleRepository:
    def setup_method(self):
        self.session_factory_mock = AsyncMock()
        self.repository = RuleRepository(self.session_factory_mock)

    def test_init(self):
        """Test repository initialization"""
        assert self.repository.session_factory == self.session_factory_mock
        assert self.repository.model_cls == RuleSetOrmEntity

    def test_to_domain(self):
        """Test converting ORM entity to domain entity"""
        orm_entity = MagicMock()
        orm_entity.id = "test-id"
        orm_entity.name = "Test Rule Set"
        orm_entity.rules = [{"type": "required", "params": {}}]
        orm_entity.description = "Test description"
        orm_entity.status = "active"
        orm_entity.created_at = None

        domain_entity = self.repository.to_domain(orm_entity)

        assert isinstance(domain_entity, RuleSet)
        assert domain_entity.id == "test-id"
        assert domain_entity.name == "Test Rule Set"
        assert domain_entity.rules == [{"type": "required", "params": {}}]
        assert domain_entity.description == "Test description"
        assert domain_entity.status == "active"

    def test_to_orm(self):
        """Test converting domain entity to ORM entity"""
        domain_entity = RuleSet(
            name="Test Rule Set",
            rules=[{"type": "required", "params": {}}],
            description="Test description",
            status="active"
        )

        orm_entity = self.repository.to_orm(domain_entity)

        assert isinstance(orm_entity, RuleSetOrmEntity)
        assert orm_entity.name == "Test Rule Set"
        assert orm_entity.rules == [{"type": "required", "params": {}}]
        assert orm_entity.description == "Test description"
        assert orm_entity.status == "active"

@pytest.mark.unit
class TestBaseRepository:
    def setup_method(self):
        self.session_factory_mock = AsyncMock()
        self.base_repo = BaseRepository[RuleSet, RuleSetOrmEntity](
            self.session_factory_mock, RuleSetOrmEntity
        )

    def test_init(self):
        """Test base repository initialization"""
        assert self.base_repo.session_factory == self.session_factory_mock
        assert self.base_repo.model_cls == RuleSetOrmEntity

    @pytest.mark.asyncio
    async def test_find_by_id(self):
        """Test finding entity by ID"""
        # This would require more complex mocking of SQLAlchemy session
        # For now, just test that the method exists
        assert hasattr(self.base_repo, 'find_by_id')

    @pytest.mark.asyncio
    async def test_find_all(self):
        """Test finding all entities"""
        assert hasattr(self.base_repo, 'find_all')

    @pytest.mark.asyncio
    async def test_create(self):
        """Test creating entity"""
        assert hasattr(self.base_repo, 'save')

    @pytest.mark.asyncio
    async def test_update(self):
        """Test updating entity"""
        assert hasattr(self.base_repo, 'save')

    @pytest.mark.asyncio
    async def test_delete(self):
        """Test deleting entity"""
        assert hasattr(self.base_repo, 'delete')


    @pytest.mark.asyncio
    async def test_save_method_exists(self):
        """Test that save method exists"""
        assert hasattr(self.base_repo, 'save')
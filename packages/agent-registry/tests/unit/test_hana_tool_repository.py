"""Unit tests for HANA tool repository query behavior."""

from contextlib import contextmanager
from unittest.mock import Mock

from app.layer1_domain.entities.tool import Tool
from app.layer1_domain.entities.tool import ToolProtocol, ToolStatus
from app.layer4_infrastructure.persistence.repositories.hana_tool_repository import HANAToolRepository


@contextmanager
def _session_scope(session):
    yield session


def _build_repository(session):
    db_factory = Mock()
    db_factory.get_session.return_value = _session_scope(session)
    return HANAToolRepository(db_factory)


def _compile_where_clause(statement) -> str:
    return str(
        statement.compile(compile_kwargs={"literal_binds": True})
    )


class TestToolRepositoryQueries:
    """Test tool repository query filters."""

    def test_find_by_id_excludes_deleted_tools(self):
        session = Mock()
        session.execute.return_value.scalar_one_or_none.return_value = None
        repository = _build_repository(session)

        repository.find_by_id("tool-1")

        statement = session.execute.call_args.args[0]
        compiled = _compile_where_clause(statement)
        assert 'tools.id = \'tool-1\'' in compiled
        assert "tools.status != 'deleted'" in compiled

    def test_find_by_ids_excludes_deleted_tools(self):
        session = Mock()
        session.execute.return_value.scalars.return_value.all.return_value = []
        repository = _build_repository(session)

        try:
            repository.find_by_ids(["tool-1"])
        except Exception:
            pass

        statement = session.execute.call_args.args[0]
        compiled = _compile_where_clause(statement)
        assert "tools.status != 'deleted'" in compiled

    def test_find_by_name_excludes_deleted_tools(self):
        session = Mock()
        session.execute.return_value.scalar_one_or_none.return_value = None
        repository = _build_repository(session)

        repository.find_by_name("tool-name")

        statement = session.execute.call_args.args[0]
        compiled = _compile_where_clause(statement)
        assert 'tools.name = \'tool-name\'' in compiled
        assert "tools.status != 'deleted'" in compiled

    def test_find_by_protocol_excludes_deleted_tools(self):
        session = Mock()
        session.execute.return_value.scalars.return_value.all.return_value = []
        repository = _build_repository(session)

        repository.find_by_protocol(ToolProtocol.REST)

        statement = session.execute.call_args.args[0]
        compiled = _compile_where_clause(statement)
        assert "tools.protocol = 'rest'" in compiled
        assert "tools.status != 'deleted'" in compiled

    def test_find_by_status_excludes_deleted_tools(self):
        session = Mock()
        session.execute.return_value.scalars.return_value.all.return_value = []
        repository = _build_repository(session)

        repository.find_by_status(ToolStatus.ACTIVE)

        statement = session.execute.call_args.args[0]
        compiled = _compile_where_clause(statement)
        assert "tools.status = 'active'" in compiled
        assert "tools.status != 'deleted'" in compiled

    def test_find_all_excludes_deleted_tools(self):
        session = Mock()
        session.execute.return_value.scalars.return_value.all.return_value = []
        repository = _build_repository(session)

        repository.find_all()

        statement = session.execute.call_args.args[0]
        compiled = _compile_where_clause(statement)
        assert "tools.status != 'deleted'" in compiled

    def test_update_excludes_deleted_tools(self, sample_tool_data):
        session = Mock()
        session.execute.return_value.scalar_one_or_none.return_value = None
        repository = _build_repository(session)
        tool = Tool(**sample_tool_data)

        try:
            repository.update(tool)
        except Exception:
            pass

        statement = session.execute.call_args.args[0]
        compiled = _compile_where_clause(statement)
        assert "tools.status != 'deleted'" in compiled

    def test_soft_delete_excludes_deleted_tools(self):
        session = Mock()
        session.execute.return_value.scalar_one_or_none.return_value = None
        repository = _build_repository(session)

        try:
            repository.soft_delete("tool-1")
        except Exception:
            pass

        statement = session.execute.call_args.args[0]
        compiled = _compile_where_clause(statement)
        assert "tools.id = 'tool-1'" in compiled
        assert "tools.status != 'deleted'" in compiled
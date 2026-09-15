import pytest

from app.layer4_frameworks.exceptions import (
    FieldAccessError,
    MappingRuleError,
    MappingWorkerError,
    TypeCoercionError,
)


class TestExceptionHierarchy:
    def test_base_exception(self):
        exc = MappingWorkerError("base")
        assert isinstance(exc, Exception)
        assert str(exc) == "base"

    def test_rule_error_inherits(self):
        exc = MappingRuleError("bad rule")
        assert isinstance(exc, MappingWorkerError)

    def test_coercion_error_inherits(self):
        exc = TypeCoercionError("bad type")
        assert isinstance(exc, MappingWorkerError)

    def test_field_access_error_inherits(self):
        exc = FieldAccessError("not found")
        assert isinstance(exc, MappingWorkerError)

    def test_can_catch_all_with_base(self):
        exceptions = [
            MappingRuleError("a"),
            TypeCoercionError("b"),
            FieldAccessError("c"),
        ]
        for exc in exceptions:
            with pytest.raises(MappingWorkerError):
                raise exc

from app.layer1_domain.value_objects.mapping_field_type import MappingFieldType
from app.layer1_domain.value_objects.mapping_mode import MappingMode


class TestMappingFieldType:
    def test_string_value(self):
        assert MappingFieldType.STRING.value == "string"

    def test_number_value(self):
        assert MappingFieldType.NUMBER.value == "number"

    def test_boolean_value(self):
        assert MappingFieldType.BOOLEAN.value == "boolean"

    def test_object_value(self):
        assert MappingFieldType.OBJECT.value == "object"

    def test_array_value(self):
        assert MappingFieldType.ARRAY.value == "array"

    def test_all_members(self):
        assert set(MappingFieldType) == {
            MappingFieldType.STRING,
            MappingFieldType.NUMBER,
            MappingFieldType.BOOLEAN,
            MappingFieldType.OBJECT,
            MappingFieldType.ARRAY,
        }

    def test_is_str(self):
        assert isinstance(MappingFieldType.STRING, str)


class TestMappingMode:
    def test_direct_value(self):
        assert MappingMode.DIRECT.value == "direct"

    def test_constant_value(self):
        assert MappingMode.CONSTANT.value == "constant"

    def test_all_members(self):
        # SA-1812: only DIRECT + CONSTANT remain (dead ARRAY_MAP removed).
        assert set(MappingMode) == {
            MappingMode.DIRECT,
            MappingMode.CONSTANT,
        }

    def test_array_map_removed(self):
        # SA-1812: the dead ARRAY_MAP member must not come back.
        assert not hasattr(MappingMode, "ARRAY_MAP")
        assert "array_map" not in {m.value for m in MappingMode}

    def test_is_str(self):
        assert isinstance(MappingMode.DIRECT, str)

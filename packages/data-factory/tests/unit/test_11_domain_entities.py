import pytest
from app.layer1_domain.entities.validation import ValidationRule, ValidationResult, ODataContent, ODataData
from app.layer1_domain.entities.transformation import TransformRule, TransformResult
from app.layer1_domain.entities.rule_management import RuleSet
from app.layer1_domain.base_audit_entity import BaseAuditDomainEntity

@pytest.mark.unit
class TestValidationEntities:
    def test_validation_rule_creation(self):
        """Test ValidationRule creation"""
        rule = ValidationRule(
            rule_name="Test Rule",
            type="required",
            params={"columns": ["id"]},
            error_message="Field is required",
            description="Test validation rule"
        )

        assert rule.rule_name == "Test Rule"
        assert rule.type == "required"
        assert rule.params == {"columns": ["id"]}
        assert rule.error_message == "Field is required"
        assert rule.description == "Test validation rule"

    def test_validation_result_creation(self):
        """Test ValidationResult creation"""
        odata = ODataContent(data=[])
        result = ValidationResult(
            total_rows=100,
            valid_rows=95,
            invalid_rows=5,
            odata=odata
        )

        assert result.total_rows == 100
        assert result.valid_rows == 95
        assert result.invalid_rows == 5
        assert result.odata == odata

    def test_odata_content_creation(self):
        """Test ODataContent creation"""
        data = [ODataData(rule_name="Rule1", violate=3, error_message="Error")]
        content = ODataContent(data=data)

        assert content.data == data

    def test_odata_data_creation(self):
        """Test ODataData creation"""
        data = ODataData(
            rule_name="Test Rule",
            violate=5,
            error_message="Validation failed"
        )

        assert data.rule_name == "Test Rule"
        assert data.violate == 5
        assert data.error_message == "Validation failed"

@pytest.mark.unit
class TestTransformationEntities:
    def test_transform_rule_creation(self):
        """Test TransformRule creation"""
        rule = TransformRule(
            rule_name="Drop Columns",
            type="drop_columns",
            params={"columns_to_drop": ["id", "timestamp"]},
            description="Remove unnecessary columns"
        )

        assert rule.rule_name == "Drop Columns"
        assert rule.type == "drop_columns"
        assert rule.params == {"columns_to_drop": ["id", "timestamp"]}
        assert rule.description == "Remove unnecessary columns"

    def test_transform_result_creation(self):
        """Test TransformResult creation"""
        result = TransformResult(
            success=True,
            total_rows=1000,
            total_columns=15,
            odata=None,
            message="Transformation completed successfully"
        )

        assert result.success is True
        assert result.total_rows == 1000
        assert result.total_columns == 15
        assert result.odata is None
        assert result.message == "Transformation completed successfully"

@pytest.mark.unit
class TestRuleManagementEntities:
    def test_rule_set_creation(self):
        """Test RuleSet creation"""
        rules = [
            {"rule_name": "Required Check", "type": "required", "params": {"columns": ["name"]}},
            {"rule_name": "Type Check", "type": "data_type", "params": {"column": "age", "expected_type": "int"}}
        ]

        rule_set = RuleSet(
            name="User Validation Rules",
            rules=rules,
            description="Rules for validating user data",
            status="active"
        )

        assert rule_set.name == "User Validation Rules"
        assert len(rule_set.rules) == 2
        assert rule_set.description == "Rules for validating user data"
        assert rule_set.status == "active"

    def test_rule_set_create_method(self):
        """Test RuleSet.create class method"""
        rules = [{"type": "required", "params": {"columns": ["email"]}}]

        rule_set = RuleSet.create(
            name="Contact Rules",
            rules=rules,
            description="Email validation rules"
        )

        assert rule_set.name == "Contact Rules"
        assert rule_set.rules == rules
        assert rule_set.description == "Email validation rules"
        assert rule_set.status == "active"  # default value

@pytest.mark.unit
class TestBaseAuditEntity:
    def test_base_audit_entity_has_required_fields(self):
        """Test that BaseAuditDomainEntity has required audit fields"""
        # This is a base class, so we can't instantiate it directly
        # But we can check that RuleSet (which inherits from it) has the fields
        rule_set = RuleSet(name="Test", rules=[])

        # These fields should exist (inherited from BaseAuditDomainEntity)
        assert hasattr(rule_set, 'id')
        assert hasattr(rule_set, 'created_at')
        assert hasattr(rule_set, 'updated_at')
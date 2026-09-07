import pytest
from app.layer4_frameworks.config.app_config import Settings
from app.layer1_domain.entities.validation import ValidationRule, ValidationResult, ODataContent, ODataData
from app.layer1_domain.entities.transformation import TransformRule, TransformResult
from app.layer1_domain.entities.rule_management import RuleSet

@pytest.mark.unit
def test_config_defaults():
    settings = Settings(APP_MODE="TEST", PORT=8000)
    assert settings.APP_MODE == "TEST"
    assert settings.PORT == 8000

@pytest.mark.unit
def test_validation_entity():
    rule = ValidationRule(rule_name="Test", type="required", params={"columns": ["id"]})
    assert rule.rule_name == "Test"

    odata = ODataData(rule_name="Test", violate=5, error_message="Err")
    content = ODataContent(data=[odata])
    res = ValidationResult(total_rows=100, valid_rows=95, invalid_rows=5, odata=content)
    assert res.invalid_rows == 5

@pytest.mark.unit
def test_transform_entity():
    rule = TransformRule(rule_name="Drop", type="drop_columns", params={"columns_to_drop": ["id"]})
    assert rule.type == "drop_columns"

    res = TransformResult(success=True, total_rows=10, total_columns=5, odata=None, message="Success")
    assert res.total_rows == 10

@pytest.mark.unit
def test_rule_management_entity():
    rs = RuleSet(id="123", name="My Rules", rules=[])
    assert rs.id == "123"

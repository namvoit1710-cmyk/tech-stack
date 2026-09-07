import pytest
import os
import json
# JSON repository removed from infrastructure
# from app.layer3_adapters.infrastructure.persistence.json_rule_repository import JsonRuleRepository
from app.layer2_application.features.rule_management.use_cases.rule_management_usecase import RuleManagementUseCase
from app.layer1_domain.entities.rule_management import RuleSet
from unittest.mock import MagicMock, AsyncMock

@pytest.fixture
def temp_db_path(tmp_path):
    p = tmp_path / "rules_test.json"
    return str(p)

@pytest.mark.integration
@pytest.mark.asyncio
async def test_json_repo_crud(temp_db_path):
    pytest.skip("JSON repository removed from infrastructure layer")
    # This test was for JSON-based persistence which has been removed
    await repo.save_raw_rules(rules)
    
    # Get
    saved = await repo.get_all_raw_rules()
    assert len(saved) == 1
    assert saved[0]["rule_name"] == "Test"

@pytest.mark.integration
@pytest.mark.asyncio
async def test_rule_matching_logic(mock_logger):
    # This tests the Use Case logic which interacts with the repo
    mock_repo = MagicMock()
    ruleset = RuleSet(
        id="test",
        name="Test Ruleset",
        rules=[{
            "rule_id": "tpl",
            "rule_name": "Check Email",
            "keywords": ["email"],
            "params": {"expression": "pl.col('(col)') > 0"} # Use legacy format to match replacement logic
        }]
    )
    mock_repo.find_all = AsyncMock(return_value=[ruleset])

    uc = RuleManagementUseCase(mock_logger, mock_repo)

    # Match headers
    matches, descs = await uc.find_matching_rules(["user_email"])


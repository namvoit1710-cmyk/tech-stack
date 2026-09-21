# Agent Registry Unit Tests

This directory contains comprehensive unit tests for the agent-registry service.

## Test Organization

Tests are organized by architectural layer:

- **Domain Layer Tests** (`test_*_entity.py`): Test domain entities (Agent, Tool, Workflow) and business logic
- **Use Case Tests** (`test_*_use_case.py`): Test application use cases with mocked dependencies
- **DTO Tests** (`test_dtos.py`): Test data transfer objects
- **Exception Tests** (`test_exceptions.py`): Test domain exceptions

## Running Tests

### Run all unit tests
```bash
pytest tests/unit
```

### Run specific test file
```bash
pytest tests/unit/test_agent_entity.py
```

### Run specific test class
```bash
pytest tests/unit/test_agent_entity.py::TestAgentEntity
```

### Run specific test
```bash
pytest tests/unit/test_agent_entity.py::TestAgentEntity::test_agent_creation
```

### Run with coverage
```bash
pytest tests/unit --cov=app --cov-report=html
```

### Run with verbose output
```bash
pytest tests/unit -v
```

### Run tests matching a pattern
```bash
pytest tests/unit -k "agent"
```

## Test Coverage

The unit tests cover:

### Domain Entities (Layer 1)
- ✅ Agent entity validation and business logic
- ✅ Tool entity validation
- ✅ Workflow entity validation
- ✅ Domain exceptions

### Use Cases (Layer 2)
- ✅ RegisterAgentUseCase
- ✅ UpdateAgentUseCase
- ✅ GetAllAgentsUseCase
- ✅ GetAgentByIdUseCase
- ✅ ActivateAgentUseCase
- ✅ PublishAgentUseCase
- ✅ RemoveAgentUseCase
- ✅ RegisterToolUseCase

### DTOs
- ✅ AgentResponseDTO
- ✅ RegisterAgentDTO
- ✅ UpdateAgentDTO

## Test Fixtures

Common test fixtures are defined in `conftest.py`:
- `sample_agent_data`: Business agent test data
- `sample_technical_agent_data`: Technical agent test data
- `sample_tool_data`: Tool test data
- `sample_workflow_data`: Workflow test data

## Writing New Tests

### Test Structure
```python
"""Unit tests for [Component]."""

import pytest
from unittest.mock import Mock

class Test[Component]:
    """Test [Component]."""

    @pytest.fixture
    def mock_repository(self):
        """Mock repository."""
        return Mock()

    def test_[scenario](self, mock_repository):
        """Test [scenario description]."""
        # Arrange
        # ...
        
        # Act
        # ...
        
        # Assert
        # ...
```

### Best Practices
1. Use descriptive test names that explain what is being tested
2. Follow Arrange-Act-Assert pattern
3. Mock external dependencies using `unittest.mock.Mock`
4. Test both success and failure scenarios
5. Test edge cases and boundary conditions
6. Keep tests isolated and independent

## Continuous Integration

Tests run automatically on:
- Pull requests
- Commits to main branch
- Pre-deployment checks

Target coverage: 80%+ for all layers

"""Unit tests for error handler middleware."""

import pytest
from unittest.mock import Mock, AsyncMock
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.layer1_domain.exceptions import (
    NotFoundException,
    AlreadyExistsException,
    InvalidDataException,
    InvalidOperationException,
)
from app.layer4_infrastructure.middleware.error_handlers import (
    domain_exception_handler,
    validation_exception_handler,
    generic_exception_handler,
)


class TestDomainExceptionHandler:
    """Test domain exception handler."""

    @pytest.fixture
    def mock_request(self):
        """Create mock request."""
        request = Mock(spec=Request)
        request.url.path = "/test/path"
        return request

    @pytest.mark.asyncio
    async def test_not_found_exception_returns_404(self, mock_request):
        """Test NotFoundException mapped to 404."""
        exc = NotFoundException("Agent", entity_id="test-id")
        
        response = await domain_exception_handler(mock_request, exc)
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
        content = response.body.decode()
        assert "NotFoundException" in content
        assert "test-id" in content

    @pytest.mark.asyncio
    async def test_already_exists_exception_returns_409(self, mock_request):
        """Test AlreadyExistsException mapped to 409."""
        exc = AlreadyExistsException("Agent", entity_name="duplicate-agent")
        
        response = await domain_exception_handler(mock_request, exc)
        
        assert response.status_code == status.HTTP_409_CONFLICT
        content = response.body.decode()
        assert "AlreadyExistsException" in content
        assert "duplicate-agent" in content

    @pytest.mark.asyncio
    async def test_invalid_data_exception_returns_400(self, mock_request):
        """Test InvalidDataException mapped to 400."""
        exc = InvalidDataException("Invalid temperature value", field="temperature")
        
        response = await domain_exception_handler(mock_request, exc)
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        content = response.body.decode()
        assert "InvalidDataException" in content
        assert "temperature" in content

    @pytest.mark.asyncio
    async def test_invalid_operation_exception_returns_400(self, mock_request):
        """Test InvalidOperationException mapped to 400."""
        exc = InvalidOperationException("Cannot activate unpublished agent")
        
        response = await domain_exception_handler(mock_request, exc)
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        content = response.body.decode()
        assert "InvalidOperationException" in content


class TestValidationExceptionHandler:
    """Test validation exception handler."""

    @pytest.fixture
    def mock_request(self):
        """Create mock request."""
        request = Mock(spec=Request)
        request.url.path = "/test/path"
        return request

    @pytest.mark.asyncio
    async def test_validation_error_returns_422(self, mock_request):
        """Test RequestValidationError mapped to 422."""
        # Create a mock validation error
        exc = RequestValidationError(
            errors=[
                {
                    "loc": ("body", "name"),
                    "msg": "field required",
                    "type": "value_error.missing",
                }
            ]
        )
        
        response = await validation_exception_handler(mock_request, exc)
        
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        content = response.body.decode()
        assert "ValidationError" in content
        assert "field required" in content

    @pytest.mark.asyncio
    async def test_validation_error_with_multiple_errors(self, mock_request):
        """Test validation error with multiple fields."""
        exc = RequestValidationError(
            errors=[
                {
                    "loc": ("body", "name"),
                    "msg": "field required",
                    "type": "value_error.missing",
                },
                {
                    "loc": ("body", "version"),
                    "msg": "invalid version format",
                    "type": "value_error",
                },
            ]
        )
        
        response = await validation_exception_handler(mock_request, exc)
        
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        content = response.body.decode()
        assert "name" in content
        assert "version" in content


class TestGenericExceptionHandler:
    """Test generic exception handler."""

    @pytest.fixture
    def mock_request(self):
        """Create mock request."""
        request = Mock(spec=Request)
        request.url.path = "/test/path"
        return request

    @pytest.mark.asyncio
    async def test_generic_exception_returns_500(self, mock_request):
        """Test generic exception mapped to 500."""
        exc = RuntimeError("Something went wrong")
        
        response = await generic_exception_handler(mock_request, exc)
        
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        content = response.body.decode()
        assert "InternalServerError" in content
        assert "unexpected error" in content.lower()

    @pytest.mark.asyncio
    async def test_generic_exception_hides_details(self, mock_request):
        """Test that generic exceptions don't leak implementation details."""
        exc = ValueError("Internal implementation detail that should not be exposed")
        
        response = await generic_exception_handler(mock_request, exc)
        
        content = response.body.decode()
        # Should not expose the original error message
        assert "Internal implementation detail" not in content
        assert "InternalServerError" in content

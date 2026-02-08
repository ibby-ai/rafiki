"""Tests for exception handler."""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from modal_backend.api.middleware import RequestIdMiddleware


def create_test_app(with_middleware: bool = True):
    """Create a FastAPI app with exception handler for testing."""
    app = FastAPI()

    if with_middleware:
        app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        """Handle uncaught exceptions with structured JSON response."""
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "request_id": request_id,
            },
        )

    @app.get("/raise-value-error")
    async def raise_value_error():
        raise ValueError("Invalid value provided")

    @app.get("/raise-runtime-error")
    async def raise_runtime_error():
        raise RuntimeError("Something went wrong")

    @app.get("/raise-key-error")
    async def raise_key_error():
        raise KeyError("missing_key")

    @app.get("/raise-type-error")
    async def raise_type_error():
        raise TypeError("Expected str, got int")

    @app.get("/raise-custom-error")
    async def raise_custom_error():
        class CustomApplicationError(Exception):
            pass

        raise CustomApplicationError("Custom error occurred")

    @app.get("/raise-empty-message")
    async def raise_empty_message():
        raise Exception()

    @app.get("/success")
    async def success_endpoint():
        return {"status": "ok"}

    return app


@pytest.fixture
def client():
    """Create a test client with middleware."""
    app = create_test_app(with_middleware=True)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_no_middleware():
    """Create a test client without middleware."""
    app = create_test_app(with_middleware=False)
    return TestClient(app, raise_server_exceptions=False)


class TestExceptionHandlerResponse:
    """Tests for exception handler response structure."""

    def test_returns_500_status_code(self, client):
        """Test that exception handler returns 500 status code."""
        response = client.get("/raise-value-error")
        assert response.status_code == 500

    def test_returns_json_content_type(self, client):
        """Test that exception handler returns JSON content type."""
        response = client.get("/raise-value-error")
        assert response.headers["content-type"] == "application/json"

    def test_response_has_ok_false(self, client):
        """Test that response has ok=False."""
        response = client.get("/raise-value-error")
        data = response.json()
        assert data["ok"] is False

    def test_response_has_error_message(self, client):
        """Test that response includes error message."""
        response = client.get("/raise-value-error")
        data = response.json()
        assert data["error"] == "Invalid value provided"

    def test_response_has_error_type(self, client):
        """Test that response includes error type."""
        response = client.get("/raise-value-error")
        data = response.json()
        assert data["error_type"] == "ValueError"

    def test_response_has_request_id(self, client):
        """Test that response includes request ID from middleware."""
        response = client.get("/raise-value-error")
        data = response.json()
        assert data["request_id"] is not None
        assert isinstance(data["request_id"], str)

    def test_response_structure_complete(self, client):
        """Test that response has all expected fields."""
        response = client.get("/raise-value-error")
        data = response.json()
        expected_fields = {"ok", "error", "error_type", "request_id"}
        assert set(data.keys()) == expected_fields


class TestExceptionHandlerErrorTypes:
    """Tests for different exception types."""

    def test_value_error(self, client):
        """Test handling ValueError."""
        response = client.get("/raise-value-error")
        data = response.json()
        assert data["error_type"] == "ValueError"
        assert data["error"] == "Invalid value provided"

    def test_runtime_error(self, client):
        """Test handling RuntimeError."""
        response = client.get("/raise-runtime-error")
        data = response.json()
        assert data["error_type"] == "RuntimeError"
        assert data["error"] == "Something went wrong"

    def test_key_error(self, client):
        """Test handling KeyError."""
        response = client.get("/raise-key-error")
        data = response.json()
        assert data["error_type"] == "KeyError"
        # KeyError wraps the key in quotes
        assert "missing_key" in data["error"]

    def test_type_error(self, client):
        """Test handling TypeError."""
        response = client.get("/raise-type-error")
        data = response.json()
        assert data["error_type"] == "TypeError"
        assert data["error"] == "Expected str, got int"

    def test_custom_error(self, client):
        """Test handling custom exception class."""
        response = client.get("/raise-custom-error")
        data = response.json()
        assert data["error_type"] == "CustomApplicationError"
        assert data["error"] == "Custom error occurred"

    def test_empty_error_message(self, client):
        """Test handling exception with empty message."""
        response = client.get("/raise-empty-message")
        data = response.json()
        assert data["error_type"] == "Exception"
        assert data["error"] == ""


class TestExceptionHandlerRequestId:
    """Tests for request ID handling in exception handler."""

    def test_request_id_included_with_middleware(self, client):
        """Test that request ID is included when middleware is present."""
        response = client.get("/raise-value-error")
        data = response.json()
        assert data["request_id"] is not None

    def test_request_id_from_header(self, client):
        """Test that custom request ID from header is preserved."""
        custom_id = "custom-error-request-123"
        response = client.get("/raise-value-error", headers={"X-Request-Id": custom_id})
        data = response.json()
        assert data["request_id"] == custom_id

    def test_request_id_none_without_middleware(self, client_no_middleware):
        """Test that request ID is None when middleware is not present."""
        response = client_no_middleware.get("/raise-value-error")
        data = response.json()
        assert data["request_id"] is None

    def test_request_id_matches_custom_header(self, client):
        """Test that custom request ID is correctly captured in error response."""
        custom_id = "trace-id-for-debugging"
        response = client.get("/raise-runtime-error", headers={"X-Request-Id": custom_id})
        data = response.json()
        # The request ID should be captured in the error response body
        assert data["request_id"] == custom_id
        assert data["error_type"] == "RuntimeError"


class TestExceptionHandlerSuccessPath:
    """Tests to ensure exception handler doesn't affect success paths."""

    def test_success_endpoint_not_affected(self, client):
        """Test that successful endpoints work normally."""
        response = client.get("/success")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_success_still_has_request_id_header(self, client):
        """Test that successful responses still have request ID header."""
        response = client.get("/success")
        assert "X-Request-Id" in response.headers


if __name__ == "__main__":
    pytest.main([__file__])

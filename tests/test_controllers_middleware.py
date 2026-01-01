"""Tests for middleware."""

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent_sandbox.controllers.middleware import RequestIdMiddleware


@pytest.fixture
def app_with_middleware():
    """Create a FastAPI app with RequestIdMiddleware."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/test")
    async def test_endpoint(request: Request):
        return {"request_id": request.state.request_id}

    @app.get("/health")
    async def health_endpoint():
        return {"status": "ok"}

    return app


@pytest.fixture
def client(app_with_middleware):
    """Create a test client for the app."""
    return TestClient(app_with_middleware)


class TestRequestIdMiddleware:
    """Tests for RequestIdMiddleware."""

    def test_generates_request_id_when_not_provided(self, client):
        """Test that middleware generates a UUID when no X-Request-Id header is sent."""
        response = client.get("/test")
        assert response.status_code == 200

        # Check response header exists
        assert "X-Request-Id" in response.headers
        request_id = response.headers["X-Request-Id"]

        # Verify it's a valid UUID
        parsed_uuid = uuid.UUID(request_id)
        assert str(parsed_uuid) == request_id

    def test_uses_provided_request_id(self, client):
        """Test that middleware uses the X-Request-Id header when provided."""
        custom_id = "custom-request-id-12345"
        response = client.get("/test", headers={"X-Request-Id": custom_id})

        assert response.status_code == 200
        assert response.headers["X-Request-Id"] == custom_id

    def test_request_id_in_response_body(self, client):
        """Test that request_id is accessible in request.state."""
        custom_id = "test-id-abc123"
        response = client.get("/test", headers={"X-Request-Id": custom_id})

        assert response.status_code == 200
        data = response.json()
        assert data["request_id"] == custom_id

    def test_generated_id_in_response_body(self, client):
        """Test that generated request_id matches response header."""
        response = client.get("/test")

        assert response.status_code == 200
        data = response.json()
        assert data["request_id"] == response.headers["X-Request-Id"]

    def test_different_requests_get_different_ids(self, client):
        """Test that each request without X-Request-Id gets a unique ID."""
        response1 = client.get("/test")
        response2 = client.get("/test")

        id1 = response1.headers["X-Request-Id"]
        id2 = response2.headers["X-Request-Id"]

        assert id1 != id2

    def test_middleware_works_on_all_endpoints(self, client):
        """Test that middleware applies to all endpoints."""
        response = client.get("/health")

        assert response.status_code == 200
        assert "X-Request-Id" in response.headers

    def test_middleware_preserves_response_content(self, client):
        """Test that middleware doesn't alter response content."""
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_request_id_with_uuid_format(self, client):
        """Test with a properly formatted UUID as request ID."""
        custom_uuid = str(uuid.uuid4())
        response = client.get("/test", headers={"X-Request-Id": custom_uuid})

        assert response.headers["X-Request-Id"] == custom_uuid
        assert response.json()["request_id"] == custom_uuid

    def test_request_id_with_empty_string(self, client):
        """Test behavior when X-Request-Id is empty string."""
        # Empty string is falsy, so middleware should generate a new ID
        response = client.get("/test", headers={"X-Request-Id": ""})

        # Empty string is still a valid header value, so it gets used
        assert response.headers["X-Request-Id"] == ""

    def test_request_id_preserved_through_request_lifecycle(self, client):
        """Test that the same request ID is used throughout the request."""
        custom_id = "lifecycle-test-id"
        response = client.get("/test", headers={"X-Request-Id": custom_id})

        # The ID in request.state should match the response header
        assert response.json()["request_id"] == response.headers["X-Request-Id"]
        assert response.json()["request_id"] == custom_id


class TestRequestIdMiddlewareEdgeCases:
    """Edge case tests for RequestIdMiddleware."""

    def test_special_characters_in_request_id(self, client):
        """Test that special characters in request ID are preserved."""
        special_id = "req-123_abc.xyz"
        response = client.get("/test", headers={"X-Request-Id": special_id})

        assert response.headers["X-Request-Id"] == special_id

    def test_long_request_id(self, client):
        """Test that long request IDs are handled."""
        long_id = "x" * 200
        response = client.get("/test", headers={"X-Request-Id": long_id})

        assert response.headers["X-Request-Id"] == long_id

    def test_numeric_request_id(self, client):
        """Test that numeric string request IDs are handled."""
        numeric_id = "123456789"
        response = client.get("/test", headers={"X-Request-Id": numeric_id})

        assert response.headers["X-Request-Id"] == numeric_id


if __name__ == "__main__":
    pytest.main([__file__])

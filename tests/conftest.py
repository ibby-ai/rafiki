"""Pytest configuration and shared fixtures."""

import os
from typing import Any

import pytest

os.environ.setdefault("INTERNAL_AUTH_SECRET", "test-internal-auth-secret")


@pytest.fixture
def mock_settings() -> dict[str, Any]:
    """Mock settings for testing."""
    return {
        "sandbox_name": "modal-backend",
        "service_port": 8001,
        "sandbox_timeout": 3600,
        "sandbox_idle_timeout": 600,
        "sandbox_cpu": 1.0,
        "sandbox_memory": 2048,
        "agent_fs_root": "/data",
        "enforce_connect_token": False,
    }


@pytest.fixture
def mock_query_body() -> dict[str, Any]:
    """Mock query body for testing."""
    return {"question": "What is the capital of France?"}

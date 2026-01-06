"""Tests for sandbox schemas."""

import pytest
from pydantic import ValidationError

from agent_sandbox.schemas import QueryBody


def test_query_body_valid():
    """Test creating a valid QueryBody."""
    body = QueryBody(question="What is the capital of France?")
    assert body.question == "What is the capital of France?"


def test_query_body_with_job_id():
    """Test that job_id is accepted for background jobs."""
    job_id = "123e4567-e89b-12d3-a456-426614174000"
    body = QueryBody(question="Run job", job_id=job_id)
    assert body.job_id == job_id


def test_query_body_empty_string():
    """Test that empty string is allowed (validation may be added later)."""
    body = QueryBody(question="")
    assert body.question == ""


def test_query_body_missing_field():
    """Test that missing question field raises ValidationError."""
    with pytest.raises(ValidationError):
        QueryBody()


def test_query_body_extra_fields_forbidden():
    """Test that extra fields are forbidden."""
    with pytest.raises(ValidationError):
        QueryBody(question="test", extra_field="not allowed")


def test_query_body_invalid_job_id():
    """Test that invalid job_id values are rejected."""
    with pytest.raises(ValidationError):
        QueryBody(question="test", job_id="../etc/passwd")


def test_query_body_whitespace_stripping():
    """Test that whitespace is stripped from strings."""
    body = QueryBody(question="  What is the capital?  ")
    assert body.question == "What is the capital?"


if __name__ == "__main__":
    pytest.main([__file__])

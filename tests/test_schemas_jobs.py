"""Tests for job schemas."""

import pytest
from pydantic import ValidationError

from agent_sandbox.schemas.jobs import (
    ArtifactEntry,
    ArtifactListResponse,
    ArtifactManifest,
    JobStatusResponse,
    JobSubmitRequest,
    WebhookConfig,
)


def test_webhook_config_minimal():
    config = WebhookConfig(url="https://example.com/webhook")
    assert str(config.url) == "https://example.com/webhook"


def test_artifact_manifest_defaults():
    manifest = ArtifactManifest()
    assert manifest.root is None
    assert manifest.files == []


def test_artifact_list_response_valid():
    manifest = ArtifactManifest(
        root="/data/jobs/job-123",
        files=[ArtifactEntry(path="report.md", size_bytes=12)],
    )
    response = ArtifactListResponse(job_id="job-123", artifacts=manifest)
    assert response.artifacts.files[0].path == "report.md"


def test_job_submit_request_with_webhook():
    request = JobSubmitRequest(
        question="Run job",
        webhook={"url": "https://example.com/webhook"},
        metadata={"source": "tests"},
    )
    assert request.question == "Run job"
    assert request.webhook is not None


def test_job_status_response_with_metrics():
    response = JobStatusResponse(
        job_id="job-123",
        status="complete",
        duration_ms=1200,
        agent_duration_ms=1100,
        tool_call_count=2,
        models=["claude-3.5-sonnet"],
    )
    assert response.duration_ms == 1200
    assert response.agent_duration_ms == 1100
    assert response.tool_call_count == 2


def test_job_status_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        JobStatusResponse(
            job_id="job-123",
            status="queued",
            extra_field="not allowed",
        )


if __name__ == "__main__":
    pytest.main([__file__])

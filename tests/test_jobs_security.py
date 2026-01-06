"""Security tests for job helpers."""

from agent_sandbox.jobs import normalize_job_id, resolve_job_artifact


def test_normalize_job_id_valid():
    job_id = "123e4567-e89b-12d3-a456-426614174000"
    assert normalize_job_id(job_id) == job_id


def test_normalize_job_id_invalid():
    assert normalize_job_id("../etc/passwd") is None


def test_resolve_job_artifact_blocks_traversal(tmp_path):
    job_id = "123e4567-e89b-12d3-a456-426614174000"
    resolved = resolve_job_artifact(str(tmp_path), job_id, "../../etc/passwd")
    assert resolved is None


def test_resolve_job_artifact_allows_relative(tmp_path):
    job_id = "123e4567-e89b-12d3-a456-426614174000"
    resolved = resolve_job_artifact(str(tmp_path), job_id, "reports/output.txt")
    assert resolved is not None
    assert str(resolved).endswith(f"{job_id}/reports/output.txt")

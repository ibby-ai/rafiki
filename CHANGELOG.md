# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] - 2026-02-04

### Added

- Cloudflare-first control plane as the primary public API surface
- KV-backed session key mapping (`session_key:<scope>:<session_key>` → `session_id`)
- Edge rate limiting via Cloudflare Rate Limiting binding
- Expanded Cloudflare documentation for auth, WebSockets, and operations

### Changed

- Public traffic now routes through Cloudflare Workers + Durable Objects
- Modal gateway endpoints are internal-only and require `X-Internal-Auth`
- Updated documentation for Phase 3 rollout status and Cloudflare usage

### Breaking Changes

- Clients must call the Cloudflare Worker URL for public API requests
- Direct access to Modal endpoints without `X-Internal-Auth` returns 401
- Session resumption should prefer `session_id` over `session_key` for stability
- Modal prompt queue endpoints removed; use Cloudflare `/session/{id}/queue`

## [0.5.0] - 2026-01-07

### Added

- **Job Artifacts**: Support for storing and retrieving job output artifacts
- **Multi-Tenancy**: Enhanced job isolation and workspace management for multiple tenants
- **Job Scheduling**: Ability to schedule jobs for future execution
- **Webhook Delivery System**: Automated webhook callbacks for job completion events with retry logic
- Comprehensive test coverage for job schemas and webhook features
- Webhook security and implementation guide documentation
- Enhanced job schema documentation with lifecycle information and examples
- Security and validation documentation to jobs.py
- Workspace and filesystem documentation to controller.py
- Enhanced configuration field descriptions in settings.py
- Comprehensive documentation to app.py helper functions
- Updated API and architecture documentation for new features

### Changed

- Enhanced controller with dedicated job workspace and improved logging
- Hardened job ID handling for better security and validation

### Fixed

- Replaced hardcoded example secrets in webhooks.py docstrings with placeholder values

## [0.3.0] - 2026-01-04

### Added

- **Job Queue System**: Async job processing via `/submit`, `/jobs/{job_id}` endpoints
  - Submit long-running tasks without blocking
  - Poll for job status and results
  - Cancel queued jobs before they start
- **Session Resumption**: `session_id`, `session_key`, `fork_session` for conversation continuity
  - Resume conversations from prior context
  - Server-side session tracking via Modal Dict
  - Fork sessions to branch conversations
- **Autoscaling Controls**: `min_containers`, `max_containers`, `scaledown_window`, `buffer_containers`
  - Keep containers warm to reduce cold starts
  - Set scale limits for cost control
  - Configure scaledown behavior
- **Resource Limits**: `cpu_limit`, `memory_limit`, `ephemeral_disk` configuration
  - Hard limits for CPU and memory
  - Ephemeral disk for function-based workloads
- **Concurrency Controls**: `max_inputs`, `target_inputs` for container concurrency
  - Control concurrent requests per container
  - Optimize autoscaling behavior
- **Retry Policies**: Exponential backoff with `retry_max_attempts`, `retry_initial_delay_ms`
  - Automatic retry for transient failures
  - Configurable backoff parameters
- **Proxy Auth**: Secure public endpoints with `Modal-Key`/`Modal-Secret` headers
  - Token-based authentication for production
  - Environment variable support for credentials
- **Volume Persistence**: Configurable `volume_commit_interval` for automatic commits
  - Persist `/data` without sandbox termination
  - Reload before queries for fresh state
- **Agent Turn Limits**: `max_turns` parameter to limit agent conversation turns
  - Prevent runaway agent loops
  - Configurable per deployment
- **Custom Domains**: `custom_domains` support for production branding
- **Service Ports**: Multiple encrypted tunnels via `service_ports`
- **Load Testing**: `load_test()` function for parallel query testing
- Comprehensive code documentation with Google-style docstrings

### Changed

- Extended `sandbox_timeout` default to 24 hours
- Set `min_containers` default to 1 (always warm)
- Improved error handling for AlreadyExistsError in sandbox creation

### Developer Experience

- Shell-based `.env` loading workflow documented
- Added ruff to dev dependencies and emphasized linting requirements
- Integrated proxy auth into Makefile curl helpers

## [0.2.1] - 2025-12-15

### Added

- Initial public release
- Short-lived and long-lived sandbox patterns
- FastAPI HTTP endpoints (`/query`, `/query_stream`, `/health`)
- MCP tool integration with ToolRegistry
- Persistent volume support (`/data`)
- Modal Connect token authentication (optional)
- Streaming responses via Server-Sent Events (SSE)

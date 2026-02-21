# PLAN_multi_agent_support

## Purpose / Big Picture
Enable the sandbox to run multiple agent SDKs while keeping Claude Agent SDK as the default. Users should be able to select a provider and optionally customize the Modal image used for that provider. The end state is a provider-agnostic runtime with consistent HTTP APIs, a default Claude path that remains unchanged for existing users, and clear extension points for other agents.

## Approach Summary / Priorities
1. **Phase 1 (Highest priority):** Image customization via a registry/factory so users can bring custom images without touching runtime behavior.
2. **Phase 2 (Medium priority):** Provider abstraction layer to make SDK swaps possible while preserving the Claude default path.
3. **Phase 3 (Lower priority):** Tool abstraction adapters to decouple MCP/tool definitions from a single SDK.

Priority order reflects risk: image customization is the lowest-risk unlock for multi-agent support; runtime abstractions are additive enhancements.

## Context and Orientation
The runtime is currently tightly coupled to Claude Agent SDK across core entry points:
- `modal_backend/api/controller.py` constructs `ClaudeAgentOptions`, instantiates `ClaudeSDKClient`, and enforces Claude permission hooks.
- `modal_backend/agent_runtime/loop.py` builds Claude options and runs `ClaudeSDKClient` directly for CLI/sandbox runs.
- `modal_backend/api/serialization.py` and `modal_backend/models/responses.py` serialize Claude SDK message classes and summary fields (e.g., `usage`, `total_cost_usd`).
- `modal_backend/mcp_tools/registry.py` and `modal_backend/mcp_tools/calculate_tool.py` use Claude SDK’s tool decorators and MCP server builder.
- `modal_backend/main.py` defines a single `agent_sdk_image` via `_base_anthropic_sdk_image()` and wires it into all Modal functions and sandboxes.
- `modal_backend/settings/settings.py` and `get_modal_secrets()` assume Anthropic-only secrets.

The architecture (see `docs/design-docs/architecture-overview.md`) relies on a long-lived Modal sandbox for the controller, with a lightweight HTTP ingress function that proxies requests to the sandbox. Any provider abstraction must preserve this shape and the existing endpoints.

## Provider Interface Spec
This spec defines the minimum surface area to swap agent SDKs while keeping API behavior stable.

### Provider Registry
- Module: `modal_backend/llm_providers/registry.py`
- Function: `get_provider(provider_id: str | None) -> AgentProvider`
- Default: `provider_id=None` resolves to the default provider (Claude).
- Source of truth: `Settings.agent_provider` with an optional per-request override in `QueryBody`.

### Core Protocols

```python
from typing import Any, AsyncIterator, Protocol

class AgentProvider(Protocol):
    provider_id: str
    display_name: str

    # Capabilities and requirements
    def capabilities(self) -> dict[str, bool]: ...
    def required_secrets(self, settings: Settings) -> list[modal.Secret]: ...

    # Image selection
    def build_image(self, settings: Settings) -> modal.Image: ...
    def image_override_allowed(self) -> bool: ...

    # Runtime
    def build_options(self, *, system_prompt: str, mcp_servers: dict[str, Any],
                      allowed_tools: list[str], session_id: str | None,
                      fork_session: bool, max_turns: int | None) -> Any: ...

    def create_client(self, options: Any) -> "AgentClient": ...

    # Serialization
    def serialize_message(self, message: Any) -> dict[str, Any]: ...
    def build_summary(self, messages: list[Any], result_message: Any | None) -> dict[str, Any]: ...

class AgentClient(Protocol):
    async def query(self, question: str) -> None: ...
    async def receive_response(self) -> AsyncIterator[Any]: ...
    async def aclose(self) -> None: ...
```

### Normalized Response Contract
- Preserve current HTTP response shape but add `provider` and `provider_payload` fields.
- `QueryResponse` should include:
  - `provider: str` (e.g., `"claude"`)
  - `provider_payload: dict[str, Any] | None` (SDK-specific metadata)
- Claude provider continues to populate `usage`, `total_cost_usd`, `structured_output`, etc.
- Non-Claude providers may place extra fields in `provider_payload` and leave unsupported summary fields as `None`.

### Tooling Adapter Contract
- Base tool definitions should be provider-agnostic (`modal_backend/mcp_tools/base.py`).
- Each provider supplies a tool adapter (e.g., `providers/claude/tools.py`) that maps base tool definitions to SDK-specific constructs.
- `get_mcp_servers()` and `get_allowed_tools()` should live behind provider APIs or be injected into `build_options()`.

### Image Customization
- Settings:
  - `agent_provider: str = "claude"`
  - `agent_image_override: str | None` (optional)
- Behavior:
  - If `agent_image_override` is set and provider allows overrides, use `modal.Image.from_registry()` or a provider-defined loader.
  - Otherwise, call provider’s `build_image()` (Claude uses the current `_base_anthropic_sdk_image()` logic).

### Request/Session Handling
- `QueryBody` gains optional `provider` and `provider_config` fields (provider-specific JSON).
- Providers declare support for `session_id` / `fork_session`; if unsupported, the controller ignores or validates accordingly.

## Plan of Work
1. **Provider abstraction layer**: Add `modal_backend/llm_providers/` with `base.py` (protocols), `registry.py`, and `claude.py` implementation. `claude.py` should wrap existing Claude SDK logic without behavior changes.
2. **Configuration updates**: Extend `modal_backend/settings/settings.py` with provider selection and image override settings; update `get_modal_secrets()` to defer to provider. Add environment variable docs in `docs/references/configuration.md` and `README.md`.
3. **Runtime refactor**: Update `modal_backend/api/controller.py` and `modal_backend/agent_runtime/loop.py` to resolve the provider, build provider options, instantiate provider client, and serialize messages via provider interfaces.
4. **Tool adapter and serialization**: Move Claude-specific serialization into provider implementation; update `modal_backend/api/serialization.py` and `modal_backend/models/responses.py` to support provider metadata while keeping existing fields stable.
5. **Modal image selection**: Update `modal_backend/main.py` to resolve the provider’s image and secrets, and to allow optional overrides. Ensure background sandbox and all Modal functions use the provider-resolved image.
6. **Docs + tests**: Add provider selection docs, update architecture diagrams to mention provider layer, and introduce tests for provider selection + default Claude path (use a mock provider or dependency injection).

## Files to Create (Planned)
- `modal_backend/llm_providers/base.py`
- `modal_backend/llm_providers/registry.py`
- `modal_backend/llm_providers/claude.py`
- `modal_backend/runtime_images/base.py` (image builder protocol)
- `modal_backend/runtime_images/__init__.py` (image factory registry)
- `modal_backend/runtime_images/claude_image.py` (extract current Claude image logic)
- `modal_backend/runtime_images/custom_image.py` (custom base image support)

## Files to Modify (Planned)
- `modal_backend/main.py` (use ImageFactory/provider image selection)
- `modal_backend/settings/settings.py` (provider + image override settings)
- `modal_backend/api/controller.py` (provider-backed runtime)
- `modal_backend/agent_runtime/loop.py` (provider-backed runtime)
- `modal_backend/api/serialization.py` (provider-aware serialization)
- `modal_backend/models/responses.py` (provider + provider_payload fields)
- `docs/references/configuration.md` / `README.md` / `docs/design-docs/architecture-overview.md`

## Concrete Steps
- (TASK_01_multi_agent_support.md) Finalize provider interface and config contract.
- (TASK_02_multi_agent_support.md) Implement provider registry + settings updates.
- (TASK_03_multi_agent_support.md) Refactor runtime paths to use provider abstraction.
- (TASK_04_multi_agent_support.md) Tool adapter + response normalization.
- (TASK_05_multi_agent_support.md) Modal image overrides, docs, and tests.

## Progress
[x] (TASK_01_multi_agent_support.md) Define provider interface and config contract.

[x] (TASK_02_multi_agent_support.md) Add provider registry and settings wiring.

[x] (TASK_03_multi_agent_support.md) Refactor controller/agent loop to provider API.

[x] (TASK_04_multi_agent_support.md) Tool adapter + response normalization.

[x] (TASK_05_multi_agent_support.md) Modal image override, docs, tests.

## Suprises & Discoveries
- Observation: None yet.
- Evidence: N/A.

## Decision Log
- Decision: Keep Claude as the default provider and preserve existing HTTP response fields for backward compatibility.
  Rationale: Avoid breaking current users and minimize migration friction.
  Date/Author: 2026-01-04 / Codex

- Decision: Introduce provider-specific payloads rather than hard-typing all providers into the schema.
  Rationale: Enables adding providers without bloating the core schema or losing metadata.
  Date/Author: 2026-01-04 / Codex

## Outcomes & Retrospective
TBD once phases are implemented.

## Implementation Order
1. Image infrastructure (ImageFactory + Claude image extraction).
2. Custom image support (custom image builder + settings wiring).
3. Provider registry + options wiring (default Claude provider).
4. Runtime refactor (controller + agent loop).
5. Tool abstraction + response normalization.
6. Docs + tests.

## Usage Examples
Default (Claude, unchanged):
```bash
modal run -m modal_backend.main
```

Custom image via environment:
```bash
export AGENT_PROVIDER=claude
export AGENT_IMAGE_OVERRIDE=my-org/my-agent:latest
export AGENT_IMAGE_SECRETS=openai-secret,my-db-secret
modal run -m modal_backend.main
```

## Testing Approach
- Unit tests for ImageFactory/provider registry resolution and option building.
- Integration test for Claude default path (ensures behavior unchanged).
- Contract test for provider response normalization (provider metadata + schema stability).

## Constraints & Considerations
- Must preserve existing Modal sandbox lifecycle and endpoints.
- Provider overrides should not bypass required secrets or permissions.
- Tool permissions and MCP servers vary across SDKs; adapters must be explicit.

## Success Criteria
1. Existing Claude Agent SDK code works unchanged.
2. Users can specify a custom Modal image via settings or environment.
3. Custom images correctly include project files and required secrets.
4. Provider selection does not break HTTP API response compatibility.
5. All execution patterns (CLI, HTTP, background) work with default and custom images.

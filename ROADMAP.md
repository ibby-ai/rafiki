# Roadmap

This roadmap outlines the planned development phases for agent-sandbox-starter. Our focus is on building production-ready infrastructure for autonomous AI agents.

## Current Status

The project currently provides:
- Modal-based sandbox execution (ephemeral and long-lived patterns)
- Claude Agent SDK integration
- MCP tool registry system
- HTTP API endpoints with streaming support
- Permission-controlled tool access

---

## Phase 1: Observability Foundation

**Priority:** High
**Status:** Planned

The ability to trace and observe agent behavior is foundational for production use.

### Goals
- [ ] Structured trace export from sandbox runs
- [ ] `/traces` endpoint for historical run retrieval
- [ ] Trace context propagation through agent loop
- [ ] Tool call logging with timing and context

### Why This Matters
> "Trace every interaction, every tool called, exact context" - without observability, you can't improve agents systematically.

### How to Contribute
- Help design the trace data model
- Implement trace middleware
- Build trace storage and retrieval APIs

---

## Phase 2: Evaluation Infrastructure

**Priority:** High
**Status:** Planned

Running evaluations over production data enables systematic improvement.

### Goals
- [ ] [Promptfoo](https://www.promptfoo.dev/docs/intro/) integration for test-driven LLM development
- [ ] YAML-based test case definitions with automated assertions
- [ ] LLM-as-judge evaluation with custom scoring prompts
- [ ] CI/CD pipeline integration for continuous evaluation
- [ ] Red-teaming capabilities for security and edge case testing
- [ ] Regression test generation from production failure cases

### Why This Matters
The difference between "works on my machine" and "works in production" requires continuous evaluation against real-world data. Promptfoo provides an open-source framework for systematic LLM testing with support for multiple providers including Anthropic.

### How to Contribute
- Design promptfoo configuration for agent evaluation
- Create test case templates for common agent patterns
- Build CI integration for automated eval runs
- Implement custom assertion functions for agent-specific behaviors

---

## Phase 3: Integration & Ecosystem

**Priority:** Medium
**Status:** Future

Connect with the broader observability and evaluation ecosystem.

### Goals
- [ ] [Braintrust](https://www.braintrust.dev/) integration for trace export and analysis
- [ ] [OpenTelemetry](https://opentelemetry.io/blog/2025/ai-agent-observability/) instrumentation with GenAI Semantic Conventions
- [ ] OpenLLMetry for automatic LLM call tracing
- [ ] Dashboard for trace exploration

### Why This Stack
Braintrust provides native OpenTelemetry support with automatic LLM span conversion, making these tools complementary. OpenTelemetry's vendor-agnostic approach prevents lock-in while enabling enterprise observability stacks.

### How to Contribute
- Implement OpenTelemetry instrumentation for agent loop
- Build Braintrust trace export adapter
- Create integration tests for trace pipelines
- Design dashboard components for trace visualization

---

## Future Considerations

These are ideas being explored but not yet planned:

- **Multi-tenant hosting** - Agent-as-a-Service platform capabilities

---

## Contributing to the Roadmap

We welcome contributions at any phase! Here's how to get involved:

1. **Pick an item** from any phase above
2. **Open an issue** to discuss your approach
3. **Check existing issues** for work in progress
4. **Join Discussions** to propose new roadmap items

See [CONTRIBUTING.md](./CONTRIBUTING.md) for development setup and guidelines.

---

## Changelog

| Date | Update |
|------|--------|
| 2026-01-01 | Initial roadmap published |

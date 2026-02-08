# Documentation Index

This directory contains comprehensive documentation for the agent sandbox application.

## First-Time Setup (15 minutes)

**New to this project?** Follow these steps to get up and running:

### Step 1: Prerequisites (5 min)

```bash
# Install Modal CLI
pip install modal

# Configure Modal (creates account if needed)
modal setup

# Create the required API secret
modal secret create anthropic-secret ANTHROPIC_API_KEY=your-key-here

# Cloudflare control plane (internal auth)
modal secret create internal-auth-secret INTERNAL_AUTH_SECRET=<same-as-cloudflare>
```

### Step 2: Verify It Works (5 min)

```bash
# Run the agent locally (short-lived sandbox)
modal run -m modal_backend.main
```

You should see the agent respond in your terminal. If you get errors, check:
- Is your Anthropic API key valid?
- Did `modal setup` complete successfully?

### Step 3: Understand the Architecture (5 min)

Read these sections from [Architecture Overview](./architecture.md):
- **High-Level Architecture** - The diagram and component overview
- **Why This Architecture?** - Understanding the trade-offs

**Key takeaway**: There are TWO services:
1. `http_app` - Lightweight HTTP gateway (public-facing)
2. Background Sandbox - Where the agent actually runs (long-lived)

### Step 4: Start Developing

```bash
# Start dev server with hot-reload
modal serve -m modal_backend.main
```

You'll get a URL like `https://<org>--test-sandbox-http-app-dev.modal.run`. Test it:

```bash
curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/query' \
  -H 'Content-Type: application/json' \
  -d '{"question":"Hello, what can you do?"}'
```

Note: `/health_check` is internal to the sandbox tunnel. Use `/health` on the gateway for public checks.

### What to Read Next

| Your Goal | Start Here |
|-----------|------------|
| Customize the agent's behavior | [Controllers](./controllers.md) |
| Add new tools | [Main README](../README.md#customization) |
| Deploy to production | [Architecture Overview](./architecture.md#deployment) |
| Integrate with your app | [API Usage Guide](./api-usage.md) |

---

## Core Documentation

### [Architecture Overview](./architecture.md)
Explains the overall system architecture, including:
- Two-tier architecture pattern (ingress + background service)
- Component responsibilities
- Request flow diagrams
- Production considerations
- When to use this pattern vs alternatives

### [Multi-Agent Architecture](./multi-agent.md)
Guide to the multi-agent system:
- Agent types (default, marketing, research)
- Core concepts: AgentConfig, AgentRegistry, AgentExecutor
- Dual orchestration: SDK native subagents vs job-based spawning
- Creating custom agents and subagents
- API usage with agent types
- Best practices for agent design

### [Controllers: Background Service](./controllers.md)
Deep dive into the controller service that runs the agent:
- What the controller does and why it exists
- How it's started and managed
- HTTP endpoints (`/health_check`, `/query`, `/query_stream`)
- Permission handling and security
- Lifecycle management
- Customization options

### [Modal Ingress](./modal-ingress.md)
Comprehensive guide to how Modal handles HTTP ingress:
- What ingress is and why it matters
- Modal's managed ingress infrastructure
- The `@modal.asgi_app()` decorator
- Request flow from client to application
- Security features (Connect tokens, SSL/TLS)
- Monitoring and troubleshooting

### [Configuration](./configuration.md)
Configuration guide covering:
- Environment variables
- Modal secrets setup
- Settings options
- Image configuration

### [API Usage Guide](./api-usage.md)
Complete guide for end users interacting with deployed endpoints:
- Deployment and public URLs
- All available endpoints with examples
- Real-world usage examples (JavaScript, Python, cURL, React)
- Authentication options
- Error handling and retry logic
- Production considerations

### [Troubleshooting Guide](./troubleshooting.md)
Solutions for common issues:
- Startup and configuration problems
- Sandbox issues (memory, timeouts, cold starts)
- File persistence problems
- Tool execution errors
- HTTP endpoint issues
- Streaming problems

### [Tool Development Guide](./tool-development.md)
Learn how to create custom tools:
- Quick start: creating your first tool
- The @tool decorator and parameter types
- Tool naming conventions
- Registering tools with the MCP server
- Best practices and testing

## Quick Reference

### Understanding the Architecture

**Start here if you want to understand how everything fits together:**
1. Read [Architecture Overview](./architecture.md) for the big picture
2. Read [Modal Ingress](./modal-ingress.md) to understand how requests arrive
3. Read [Controllers](./controllers.md) to understand how requests are processed

### For Developers

**Adding new features or modifying behavior:**
- [Controllers](./controllers.md) - How to customize the agent service
- [Configuration](./configuration.md) - How to adjust settings
- [Architecture Overview](./architecture.md) - Understanding the system before making changes

### For Operations

**Deploying and maintaining the service:**
- [Architecture Overview](./architecture.md) - Production considerations
- [Modal Ingress](./modal-ingress.md) - Monitoring and troubleshooting
- [Configuration](./configuration.md) - Environment setup

### For End Users

**Using the deployed API:**
- [API Usage Guide](./api-usage.md) - Complete guide to using the endpoints
- [Architecture Overview](./architecture.md) - Understanding how it works

## Key Concepts

### Ingress
The infrastructure that accepts incoming HTTP/HTTPS requests and routes them to your application. Modal handles this automatically - see [Modal Ingress](./modal-ingress.md).

### Controller
The long-lived background service that actually runs the Claude Agent SDK. It's a FastAPI microservice running inside a Modal Sandbox - see [Controllers](./controllers.md).

### Two-Tier Architecture
The pattern used in this application:
- **Tier 1:** Lightweight `http_app` (ingress handler) that receives requests
- **Tier 2:** Long-lived background sandbox (controller) that processes them

This pattern optimizes for low latency and resource efficiency - see [Architecture Overview](./architecture.md).

## Related Resources

- [Main README](../README.md) - Quickstart and project overview
- [Modal Documentation](https://modal.com/docs) - Official Modal platform docs
- [Claude Agent SDK Documentation](https://docs.claude.com/en/api/agent-sdk/python) - Agent SDK reference

# Documentation Index

This directory contains comprehensive documentation for the agent sandbox application.

## Core Documentation

### [Architecture Overview](./architecture.md)
Explains the overall system architecture, including:
- Two-tier architecture pattern (ingress + background service)
- Component responsibilities
- Request flow diagrams
- Production considerations
- When to use this pattern vs alternatives

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


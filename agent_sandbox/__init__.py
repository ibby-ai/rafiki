"""
Agent Sandbox Starter Package

This package provides a Modal-based agent sandbox with HTTP endpoints,
background services, and tool integrations.

Main entry points:
- `agent_sandbox.app`: Main application with all Modal functions
- `agent_sandbox.deploy`: Deployment composition for production

Usage:
    # Run locally
    modal run -m agent_sandbox.app

    # Serve with hot reload
    modal serve -m agent_sandbox.app

    # Deploy to production
    modal deploy -m agent_sandbox.deploy
"""

# Import submodules to ensure all Modal functions are registered
from agent_sandbox import (
    agents,
    app,
    config,
    controllers,
    images,
    prompts,
    providers,
    schemas,
    tools,
)

__all__ = [
    "app",
    "agents",
    "controllers",
    "config",
    "prompts",
    "images",
    "providers",
    "schemas",
    "tools",
]

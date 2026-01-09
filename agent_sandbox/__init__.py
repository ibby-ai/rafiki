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
# Note: tools module requires claude_agent_sdk which is only available in Modal containers
from agent_sandbox import agents, config, controllers, prompts, schemas

try:
    from agent_sandbox import app, tools

    __all__ = [
        "app",
        "agents",
        "controllers",
        "config",
        "prompts",
        "schemas",
        "tools",
    ]
except ImportError:
    # claude_agent_sdk not available (local development/testing)
    __all__ = [
        "agents",
        "controllers",
        "config",
        "prompts",
        "schemas",
    ]

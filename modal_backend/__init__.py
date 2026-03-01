"""
Rafiki Package

This package provides a Modal-based Rafiki runtime with HTTP endpoints,
background services, and tool integrations.

Main entry points:
- `modal_backend.main`: Main application with all Modal functions
- `modal_backend.deploy`: Deployment composition for production

Usage:
    # Run locally
    modal run -m modal_backend.main

    # Serve with hot reload
    modal serve -m modal_backend.main

    # Deploy to production
    modal deploy -m modal_backend.deploy
"""

# Import submodules to ensure all Modal functions are registered
# Note: mcp_tools module depends on openai-agents runtime extras in container builds.
from modal_backend import agent_runtime, api, instructions, models, settings

try:
    from modal_backend import main, mcp_tools

    __all__ = [
        "main",
        "agent_runtime",
        "api",
        "settings",
        "instructions",
        "models",
        "mcp_tools",
    ]
except ImportError:
    # openai-agents runtime dependencies not available (local development/testing)
    __all__ = [
        "agent_runtime",
        "api",
        "settings",
        "instructions",
        "models",
    ]

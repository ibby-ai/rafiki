"""LangSmith tracing for Claude Agent SDK.

Call ensure_langsmith_configured() once per process before creating
ClaudeSDKClient or using query(). Safe to call multiple times; only
configures on first call.
"""

import logging
import os

from agent_sandbox.config.settings import get_settings

_LANGSMITH_CONFIGURED = False
_logger = logging.getLogger(__name__)


def ensure_langsmith_configured() -> None:
    """Configure Claude Agent SDK to trace to LangSmith. Idempotent."""
    global _LANGSMITH_CONFIGURED
    if _LANGSMITH_CONFIGURED:
        return

    settings = get_settings()
    if not settings.enable_langsmith_tracing:
        return

    if not os.getenv("LANGSMITH_API_KEY"):
        _logger.warning(
            "LangSmith tracing enabled but LANGSMITH_API_KEY is not set; "
            "skipping tracing configuration."
        )
        return

    try:
        from langsmith.integrations.claude_agent_sdk import configure_claude_agent_sdk
    except ImportError:
        _logger.warning(
            "LangSmith tracing enabled but langsmith is not installed; "
            "skipping tracing configuration."
        )
        return

    configure_claude_agent_sdk()
    _LANGSMITH_CONFIGURED = True

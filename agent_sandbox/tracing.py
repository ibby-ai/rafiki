"""LangSmith tracing for Claude Agent SDK.

Call ensure_langsmith_configured() once per process before creating
ClaudeSDKClient or using query(). Safe to call multiple times; only
configures on first call.
"""

_LANGSMITH_CONFIGURED = False


def ensure_langsmith_configured() -> None:
    """Configure Claude Agent SDK to trace to LangSmith. Idempotent."""
    global _LANGSMITH_CONFIGURED
    if _LANGSMITH_CONFIGURED:
        return
    try:
        from langsmith.integrations.claude_agent_sdk import configure_claude_agent_sdk

        configure_claude_agent_sdk()
        _LANGSMITH_CONFIGURED = True
    except ImportError:
        pass

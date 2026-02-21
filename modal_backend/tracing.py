"""LangSmith tracing for OpenAI Agents SDK.

Call ensure_langsmith_configured() once per process before creating agents/runners.
Safe to call multiple times; configuration is applied only once.
"""

import contextlib
import logging
import os
from collections.abc import Iterator
from typing import Any

from modal_backend.settings.settings import get_settings

_LANGSMITH_CONFIGURED = False
_logger = logging.getLogger(__name__)


def ensure_langsmith_configured() -> None:
    """Configure OpenAI Agents SDK tracing to LangSmith. Idempotent."""
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
        from agents import set_trace_processors
        from langsmith.integrations.openai_agents_sdk import OpenAIAgentsTracingProcessor
    except ImportError:
        _logger.warning(
            "LangSmith tracing enabled but openai-agents or langsmith integration is missing; "
            "skipping tracing configuration."
        )
        return

    set_trace_processors([OpenAIAgentsTracingProcessor()])
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    _LANGSMITH_CONFIGURED = True
    _logger.info("LangSmith tracing processor configured for OpenAI Agents SDK")


@contextlib.contextmanager
def langsmith_run_context(metadata: dict[str, Any] | None = None) -> Iterator[None]:
    """Attach best-effort metadata context for LangSmith traces."""
    if not metadata:
        yield
        return

    settings = get_settings()
    if not settings.enable_langsmith_tracing:
        yield
        return

    try:
        from langsmith import tracing_context
    except ImportError:
        yield
        return

    try:
        with tracing_context(
            metadata=metadata,
            tags=["openai-agents-sdk", "modal-backend"],
        ):
            yield
    except Exception:
        _logger.warning("Failed to initialize LangSmith tracing context", exc_info=True)
        yield

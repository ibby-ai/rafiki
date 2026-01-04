"""Provider-agnostic tool definitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolDefinition:
    """Definition for a tool independent of any SDK."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    server: str = "utilities"

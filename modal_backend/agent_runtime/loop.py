"""Thin CLI wrapper for single-shot agent execution.

This module is invoked in two ways:
- From a Modal sandbox via `sb.exec("python", "-m", "modal_backend.agent_runtime.loop", ...)`
- Directly as a script (`python -m modal_backend.agent_runtime.loop --question ...`) for local testing.

It uses the AgentConfig system to configure agent behavior and supports
multiple agent types via the --agent-type argument.

For the core agent configuration and execution logic, see:
- modal_backend.agent_runtime.base: AgentConfig, build_agent_options
- modal_backend.agent_runtime.registry: get_agent_config, list_agent_types
"""

import argparse

import anyio
from claude_agent_sdk import ClaudeSDKClient

from modal_backend.agent_runtime.base import build_agent_options
from modal_backend.agent_runtime.registry import get_agent_config, list_agent_types
from modal_backend.instructions.prompts import DEFAULT_QUESTION
from modal_backend.settings.settings import get_settings

_settings = get_settings()


# Re-export build_agent_options for backward compatibility with imports like:
# from modal_backend.agent_runtime.loop import build_agent_options
# This is deprecated - prefer importing from modal_backend.agent_runtime or modal_backend.agent_runtime.base
__all__ = ["build_agent_options", "run_agent"]


async def run_agent(
    question: str = DEFAULT_QUESTION,
    agent_type: str = "default",
    session_id: str | None = None,
    fork_session: bool = False,
):
    """Execute a single agent query and print the streamed response.

    Args:
        question: Natural-language input to pass to the agent.
        agent_type: Type of agent to use (e.g., "default", "marketing", "research").
        session_id: Optional session ID to resume from.
        fork_session: Whether to fork the session.
    """
    # Get agent configuration
    config = get_agent_config(agent_type)

    # Determine max_turns from config or settings
    max_turns = config.max_turns or _settings.agent_max_turns

    # Build options using agent config
    options = build_agent_options(
        config.get_mcp_servers(),
        config.get_effective_allowed_tools(),
        config.system_prompt,
        subagents=config.get_subagents(),
        resume=session_id,
        fork_session=fork_session,
        max_turns=max_turns,
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(question)

        # Extract and print response
        async for msg in client.receive_response():
            print(msg)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run a single-shot agent query",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Agent Types:
  {", ".join(list_agent_types())}

Examples:
  python -m modal_backend.agent_runtime.loop --question "What is Python?"
  python -m modal_backend.agent_runtime.loop --agent-type marketing --question "Write a tagline"
  python -m modal_backend.agent_runtime.loop --agent-type research --question "Research AI trends"
""",
    )
    parser.add_argument("--question", type=str, default=DEFAULT_QUESTION)
    parser.add_argument(
        "--agent-type",
        type=str,
        default="default",
        help=f"Agent type to use. Available: {', '.join(list_agent_types())}",
    )
    parser.add_argument("--session-id", type=str, default=None)
    parser.add_argument("--fork-session", action="store_true")
    args = parser.parse_args()

    anyio.run(run_agent, args.question, args.agent_type, args.session_id, args.fork_session)

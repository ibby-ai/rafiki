"""Thin CLI wrapper for single-shot agent execution."""

import argparse

import anyio
from agents import Runner

from modal_backend.agent_runtime.base import build_agent_options, ensure_session
from modal_backend.agent_runtime.registry import get_agent_config, list_agent_types
from modal_backend.instructions.prompts import DEFAULT_QUESTION
from modal_backend.settings.settings import get_settings

_settings = get_settings()

__all__ = ["build_agent_options", "run_agent"]


async def run_agent(
    question: str = DEFAULT_QUESTION,
    agent_type: str = "default",
    session_id: str | None = None,
    fork_session: bool = False,
):
    """Execute a single agent query and print the response."""
    config = get_agent_config(agent_type)
    max_turns = config.max_turns or _settings.agent_max_turns or 50

    agent = build_agent_options(
        config.get_mcp_servers(),
        config.get_effective_allowed_tools(),
        config.system_prompt,
        subagents=config.get_subagents(),
    )

    session, resolved_session_id = await ensure_session(
        session_id,
        fork_session=fork_session,
        db_path=_settings.openai_session_db_path,
    )

    result = await Runner.run(agent, question, session=session, max_turns=max_turns)
    print(f"session_id={resolved_session_id}")
    print(result.final_output)


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

"""Subagent prompts for SDK native multi-agent orchestration.

These prompts are used by AgentDefinition subagents, which run in-process
within the same SDK client as the lead agent. This enables low-latency
delegation via the built-in Task tool.

Exports:
    RESEARCHER_PROMPT: Prompt for research/web search subagent
    DATA_ANALYST_PROMPT: Prompt for data analysis subagent
    REPORT_WRITER_PROMPT: Prompt for report synthesis subagent
"""

from agent_sandbox.prompts.subagents.data_analyst import DATA_ANALYST_PROMPT
from agent_sandbox.prompts.subagents.report_writer import REPORT_WRITER_PROMPT
from agent_sandbox.prompts.subagents.researcher import RESEARCHER_PROMPT

__all__ = ["RESEARCHER_PROMPT", "DATA_ANALYST_PROMPT", "REPORT_WRITER_PROMPT"]

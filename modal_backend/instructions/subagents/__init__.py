"""Subagent prompts for SDK native multi-agent orchestration.

These prompts are used by OpenAI Agents handoff targets, which run in-process
within the same SDK runtime as the lead agent. This enables low-latency
delegation via handoff tools.

Exports:
    RESEARCHER_PROMPT: Prompt for research/web search subagent
    DATA_ANALYST_PROMPT: Prompt for data analysis subagent
    REPORT_WRITER_PROMPT: Prompt for report synthesis subagent
"""

from modal_backend.instructions.subagents.data_analyst import DATA_ANALYST_PROMPT
from modal_backend.instructions.subagents.report_writer import REPORT_WRITER_PROMPT
from modal_backend.instructions.subagents.researcher import RESEARCHER_PROMPT

__all__ = ["RESEARCHER_PROMPT", "DATA_ANALYST_PROMPT", "REPORT_WRITER_PROMPT"]

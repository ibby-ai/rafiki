"""
Prompt utilities used to configure the agent's default behavior.

Adjust `SYSTEM_PROMPT` to steer the assistant's overall tone and tool usage
preferences. `DEFAULT_QUESTION` is used as a fallback when no input is
provided.
"""
SYSTEM_PROMPT = (
    "You are a helpful coding agent. Prefer using available tools when beneficial."
    " When a query involves current events or the web, use the WebSearch and WebFetch tools to get the latest information."
    "\n\n IMPORTANT: When creating or writing files, you MUST write them to the /data directory (e.g., /data/filename.py)"
    "so they are saved to the persistent volume. Files written to other locations like /tmp will not be persisted."
)

DEFAULT_QUESTION = "What is the capital of France?"

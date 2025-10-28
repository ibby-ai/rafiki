"""
The code responsible for running the agent.
"""
from fastapi import FastAPI
from pydantic import BaseModel
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
import anyio
from typing import Any, Dict, List
from utils.prompts import SYSTEM_PROMPT
from utils.tools import MCP_SERVERS, ALLOWED_TOOLS
from utils.prompts import DEFAULT_QUESTION
import argparse

app = FastAPI()

class QueryBody(BaseModel):
    question: str = DEFAULT_QUESTION

# Use the custom tools with Claude
def _options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers=MCP_SERVERS,
        allowed_tools=ALLOWED_TOOLS,
    )


@app.get("/health_check")
def health_check():
    return {"ok": True}

@app.post("/query")
async def query_agent(body: QueryBody):
    result: Dict[str, Any] = {"ok": True, "messages": []}
    async with ClaudeSDKClient(options=_options()) as client:
        await client.query(body.question)
        async for msg in client.receive_response():
            result["messages"].append(str(msg))
    return result


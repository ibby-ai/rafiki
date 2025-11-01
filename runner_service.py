"""
FastAPI microservice that runs inside a long-lived Modal Sandbox.

This service exposes two endpoints:
- `GET /health_check` used by the controller to know when the service is ready.
- `POST /query` which streams a response from the Claude Agent SDK.

This file is started inside the sandbox via `uvicorn runner_service:app` (see
`main.get_or_start_background_sandbox`). The sandbox is created with an
encrypted port (8001), and `main.test_endpoint` proxies to `/query` at that
URL.

See Modal docs for details about `modal.Sandbox`, encrypted ports, and tunnel
discovery.

Important:
- To reach this service from outside (via `main.test_endpoint`), make sure the
  app is running with `modal serve main.py` (dev) or has been deployed with
  `modal deploy main.py`.
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
    """Request payload for `/query`.

    Attributes:
        question: Natural-language prompt to send to the agent.
    """

# Use the custom tools with Claude
def _options() -> ClaudeAgentOptions:
    """Build default `ClaudeAgentOptions` used by this service.

    Returns:
        A configured `ClaudeAgentOptions` instance using our local MCP servers,
        allowed tools, and `SYSTEM_PROMPT`.
    """
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers=MCP_SERVERS,
        allowed_tools=ALLOWED_TOOLS,
        # Running in a sandbox, so we can bypass permissions
        # Making the agent truly autonomous
        permission_mode="bypassPermissions"
    )


@app.get("/health_check")
def health_check():
    """Liveness/readiness probe.

    Uvicorn starts quickly, but downstream dependencies may still be warming
    up. We return a simple OK when the process is ready to receive traffic.

    Curl example (using the discovered `${SERVICE_URL}` from the sandbox):

        ```bash
        curl -sS "${SERVICE_URL}/health_check"
        ```
    """
    return {"ok": True}

@app.post("/query")
async def query_agent(body: QueryBody):
    """Run a single agent query and stream back messages as strings.

    Args:
        body: `QueryBody` containing the question to ask the agent.

    Returns:
        A JSON-serializable dict with `ok` and a list of response `messages`.

    Curl example (using the discovered `${SERVICE_URL}` from the sandbox):

        ```bash
        curl -X POST "${SERVICE_URL}/query" \
          -H 'Content-Type: application/json' \
          -d '{"question":"What is the capital of Canada?"}'
        ```
    """
    result: Dict[str, Any] = {"ok": True, "messages": []}
    async with ClaudeSDKClient(options=_options()) as client:
        await client.query(body.question)
        async for msg in client.receive_response():
            result["messages"].append(str(msg))
    return result


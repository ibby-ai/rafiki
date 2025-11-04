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
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from claude_agent_sdk import (
    ClaudeSDKClient, 
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate,
    ToolPermissionContext,
)
from claude_agent_sdk.types import PermissionRuleValue
from typing import Any, Dict
from utils.tools import MCP_SERVERS, ALLOWED_TOOLS
from utils.prompts import DEFAULT_QUESTION, SYSTEM_PROMPT

app = FastAPI()
ENFORCE_CONNECT_TOKEN = False

class QueryBody(BaseModel):
    question: str = DEFAULT_QUESTION
    """Request payload for `/query`.

    Attributes:
        question: Natural-language prompt to send to the agent.
    """

async def allow_web_only(
    tool_name: str,
    tool_input: Dict[str, Any],
    ctx: ToolPermissionContext,
):
    if tool_name.startswith("WebSearch") or tool_name.startswith("WebFetch"):
        return PermissionResultAllow(updated_input=tool_input)
    return PermissionResultDeny(message=f"Tool {tool_name} is not allowed")


#  Not being used yet. Experimenting with different permission modes.
async def allow_web_only_with_updates(
    tool_name: str,
    tool_input: Dict[str, Any],
):
    if tool_name.startswith("WebSearch") or tool_name.startswith("WebFetch"):
        updates = [
            PermissionUpdate(
                type="addRules",
                rules=[
                    PermissionRuleValue(tool_name="WebSearch(*)"),
                    PermissionRuleValue(tool_name="WebFetch(*)"),
                ],
                behavior="allow",
                destination="session",
            )
        ]
        return PermissionResultAllow(
            updated_input=tool_input,
            updated_permissions=updates,
        )
    return PermissionResultDeny(message=f"Tool {tool_name} is not allowed")

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
        #permission_mode="bypassPermissions" # Not allowed when have root access
        can_use_tool=allow_web_only,
        permission_mode="acceptEdits"
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
async def query_agent(body: QueryBody, request: Request):
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
    if ENFORCE_CONNECT_TOKEN:
        # Modal injects this header when a valid connect token is presented
        if not request.headers.get("X-Verified-User-Data"):
            raise HTTPException(status_code=401, detail="Missing or invalid connect token")

    result: Dict[str, Any] = {"ok": True, "messages": []}
    async with ClaudeSDKClient(options=_options()) as client:
        await client.query(body.question)
        async for msg in client.receive_response():
            result["messages"].append(str(msg))
    return result


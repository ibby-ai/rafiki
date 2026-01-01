"""
FastAPI microservice that runs inside a long-lived Modal Sandbox.

This service exposes endpoints:
- `GET /health_check` used by the controller to know when the service is ready.
- `POST /query` which returns a response from the Claude Agent SDK.
- `POST /query_stream` which streams a response from the Claude Agent SDK.

This file is started inside the sandbox via `uvicorn agent_sandbox.controllers.controller:app`
(see `agent_sandbox.app.get_or_start_background_sandbox`). The sandbox is created with an
encrypted port (8001), and `agent_sandbox.app.http_app` proxies to these endpoints.

See Modal docs for details about `modal.Sandbox`, encrypted ports, and tunnel
discovery.

Important:
- To reach this service from outside (via `agent_sandbox.app.http_app`), make sure the
  app is running with `modal serve -m agent_sandbox.app` (dev) or has been deployed with
  `modal deploy -m agent_sandbox.deploy`.
"""

from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)
from fastapi import FastAPI, HTTPException, Request
from starlette.responses import StreamingResponse

from agent_sandbox.prompts.prompts import SYSTEM_PROMPT
from agent_sandbox.schemas import QueryBody
from agent_sandbox.tools import get_allowed_tools, get_mcp_servers

app = FastAPI()
ENFORCE_CONNECT_TOKEN = False


async def allow_web_only(
    tool_name: str,
    tool_input: dict[str, Any],
    ctx: ToolPermissionContext,
):
    """Permission handler that allows only web-related tools.

    Args:
        tool_name: Name of the tool being requested.
        tool_input: Input parameters for the tool.
        ctx: Permission context.

    Returns:
        PermissionResultAllow if tool is web-related, otherwise PermissionResultDeny.
    """
    if tool_name.startswith("WebSearch") or tool_name.startswith("WebFetch"):
        return PermissionResultAllow(updated_input=tool_input)
    return PermissionResultDeny(message=f"Tool {tool_name} is not allowed")


def _options() -> ClaudeAgentOptions:
    """Build default `ClaudeAgentOptions` used by this service.

    Returns:
        A configured `ClaudeAgentOptions` instance using our local MCP servers,
        allowed tools, and `SYSTEM_PROMPT`.
    """
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers=get_mcp_servers(),
        allowed_tools=get_allowed_tools(),
        # Running in a sandbox, so we can bypass permissions
        # Making the agent truly autonomous
        # permission_mode="bypassPermissions" # Not allowed when have root access
        can_use_tool=allow_web_only,
        permission_mode="acceptEdits",
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

    result: dict[str, Any] = {"ok": True, "messages": []}
    async with ClaudeSDKClient(options=_options()) as client:
        await client.query(body.question)
        async for msg in client.receive_response():
            result["messages"].append(str(msg))
    return result


@app.post("/query_stream")
async def query_agent_stream(body: QueryBody, request: Request):
    """Stream agent responses as Server-Sent Events (SSE).

    Args:
        body: `QueryBody` containing the question to ask the agent.
        request: FastAPI request object.

    Returns:
        StreamingResponse with text/event-stream content type.
    """
    if ENFORCE_CONNECT_TOKEN:
        if not request.headers.get("X-Verified-User-Data"):
            raise HTTPException(status_code=401, detail="Missing or invalid connect token")

    async def sse():
        async with ClaudeSDKClient(options=_options()) as client:
            await client.query(body.question)
            async for msg in client.receive_response():
                # Emit each message chunk as an SSE event
                yield f"data: {str(msg)}\n\n"
        # Signal completion (optional)
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")

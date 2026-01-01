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

import json
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)
from claude_agent_sdk.types import Message, ResultMessage
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from agent_sandbox.config.settings import get_settings
from agent_sandbox.controllers.middleware import RequestIdMiddleware
from agent_sandbox.controllers.serialization import (
    build_final_summary,
    iter_text_blocks,
    serialize_message,
)
from agent_sandbox.prompts.prompts import SYSTEM_PROMPT
from agent_sandbox.schemas import QueryBody
from agent_sandbox.schemas.responses import ErrorResponse, QueryResponse
from agent_sandbox.tools import get_allowed_tools, get_mcp_servers

app = FastAPI()
app.add_middleware(RequestIdMiddleware)
_settings = get_settings()


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


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions with structured JSON response.

    Args:
        request: The incoming request.
        exc: The exception that was raised.

    Returns:
        JSONResponse with error details and request ID.
    """
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "request_id": request_id,
        },
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


@app.post(
    "/query",
    response_model=QueryResponse,
    responses={500: {"model": ErrorResponse}},
)
async def query_agent(body: QueryBody, request: Request) -> QueryResponse:
    """Run a single agent query and return structured messages.

    Args:
        body: `QueryBody` containing the question to ask the agent.

    Returns:
        QueryResponse with `ok`, `messages` list, and `summary`.

    Curl example (using the discovered `${SERVICE_URL}` from the sandbox):

        ```bash
        curl -X POST "${SERVICE_URL}/query" \
          -H 'Content-Type: application/json' \
          -d '{"question":"What is the capital of Canada?"}'
        ```
    """
    if _settings.enforce_connect_token:
        # Modal injects this header when a valid connect token is presented
        if not request.headers.get("X-Verified-User-Data"):
            raise HTTPException(status_code=401, detail="Missing or invalid connect token")

    messages: list[Message] = []
    result_message: ResultMessage | None = None
    async with ClaudeSDKClient(options=_options()) as client:
        await client.query(body.question)
        async for msg in client.receive_response():
            messages.append(msg)
            if isinstance(msg, ResultMessage):
                result_message = msg

    text_blocks = iter_text_blocks(messages)
    final_text = None
    if result_message and result_message.result:
        final_text = result_message.result
    elif text_blocks:
        final_text = "\n".join(text_blocks)

    return {
        "ok": True,
        "messages": [serialize_message(message) for message in messages],
        "summary": build_final_summary(result_message, final_text),
    }


@app.post("/query_stream")
async def query_agent_stream(body: QueryBody, request: Request):
    """Stream agent responses as Server-Sent Events (SSE).

    Args:
        body: `QueryBody` containing the question to ask the agent.
        request: FastAPI request object.

    Returns:
        StreamingResponse with text/event-stream content type.
    """
    if _settings.enforce_connect_token:
        if not request.headers.get("X-Verified-User-Data"):
            raise HTTPException(status_code=401, detail="Missing or invalid connect token")

    def _format_sse(event: str, data: dict[str, Any]) -> str:
        payload = json.dumps(data, ensure_ascii=True)
        return f"event: {event}\ndata: {payload}\n\n"

    async def sse():
        messages: list[Message] = []
        result_message: ResultMessage | None = None
        async with ClaudeSDKClient(options=_options()) as client:
            await client.query(body.question)
            async for msg in client.receive_response():
                messages.append(msg)
                if isinstance(msg, ResultMessage):
                    result_message = msg
                serialized = serialize_message(msg)
                yield _format_sse(serialized["type"], serialized)

        text_blocks = iter_text_blocks(messages)
        final_text = None
        if result_message and result_message.result:
            final_text = result_message.result
        elif text_blocks:
            final_text = "\n".join(text_blocks)

        yield _format_sse("done", build_final_summary(result_message, final_text))

    return StreamingResponse(sse(), media_type="text/event-stream")

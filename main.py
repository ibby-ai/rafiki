"""
Entry-point and Modal function definitions for running the agent in a sandboxed
environment and exposing lightweight HTTP endpoints.

Quickstart (CLI):
- run local_entrypoint: `modal run main.py`
- run sandbox_controller: `modal run main.py::sandbox_controller --question "..."`
- run run_agent_remote: `modal run main.py::run_agent_remote --question "..."`
- keep dev deployment running: `modal serve main.py`
- deploy to production: `modal deploy main.py`

Notes for future maintainers:
- This file defines a `modal.App` plus several `@app.function` entries. Functions
  annotated with `@modal.fastapi_endpoint` are exposed as HTTP endpoints when the
  app is served or deployed. See Modal docs for `modal.App`, `@app.function`,
  `modal.Sandbox`, and `@modal.fastapi_endpoint` for lifecycle and limits.
- We use a long-running `modal.Sandbox` process to host a FastAPI microservice
  (`runner_service.py`) and then call into it from a short-lived Modal
  function. This pattern keeps cold-start latency low for the model runtime
  while allowing us to keep the HTTP frontdoor responsive.

Prerequisite for curl testing:
- Start the dev server locally with `modal serve main.py` so the HTTP endpoint
  (see `test_endpoint`) is reachable at a dev URL like
  `https://<org>--test-sandbox-test-endpoint-dev.modal.run`.
"""

import modal
from utils.env_templates import get_env_template
from utils.prompts import DEFAULT_QUESTION
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import urllib.request
import time
import json
import httpx
import socket

app = modal.App("test-sandbox")

web_app = FastAPI()

web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


agent_sdk_env = get_env_template("base-anthropic-sdk")
# Global handles to a background sandbox and its encrypted tunnel URL. We keep
# these in module scope so repeated calls within the same worker reuse the
# long-lived process.
SANDBOX: Optional[modal.Sandbox] = None
SERVICE_URL: Optional[str] = None

def _wait_for_service(url: str, timeout: int = 60, path: str = "/health_check") -> None:
    """Block until an HTTP health check returns 200 OK.

    - Builds the absolute check URL by appending `path` to the provided base
      `url` (which should include scheme and host from the sandbox tunnel).
    - Polls until `timeout` seconds have elapsed.

    Raises:
        TimeoutError: If the service does not become healthy in time.
    """
    check_url = f"{url.rstrip('/')}{path}"
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(check_url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, socket.timeout):
            time.sleep(1)
    raise TimeoutError(f"Service {check_url} did not become available within {timeout} seconds")

def get_or_start_background_sandbox() -> tuple[modal.Sandbox, str]:
    """Return a running background sandbox and its encrypted service URL.

    Starts a daemonized sandbox running `uvicorn runner_service:app` if one is
    not already available, then discovers its encrypted tunnel URL on port
    8001. The function blocks until the `/health_check` endpoint responds.

    Returns:
        A pair of `(sandbox, service_url)`.

    See:
        Modal docs on `modal.Sandbox.create`, tunnels, and encrypted ports.

    Examples:
        - Trigger sandbox creation via the Modal HTTP endpoint (on-demand):

          ```bash
          curl -X POST 'https://<org>--test-sandbox-test-endpoint-dev.modal.run' \
            -H 'Content-Type: application/json' \
            -d '"What is the capital of Canada?"'
          ```

        - Query the sandboxed service directly when you have `SERVICE_URL`:

          ```bash
          # Liveness check
          curl -sS "${SERVICE_URL}/health_check"

          # Ask a question via the service API
          curl -X POST "${SERVICE_URL}/query" \
            -H 'Content-Type: application/json' \
            -d '{"question":"What is the capital of Canada?"}'
          ```
    Prerequisite:
        Ensure the HTTP endpoint is live by running `modal serve main.py` (dev)
        or deploying with `modal deploy main.py`.
    """
    global SANDBOX, SERVICE_URL
    if SANDBOX is not None and SERVICE_URL:
        return SANDBOX, SERVICE_URL
    SANDBOX = modal.Sandbox.create(
        # Command to run persistently in the background
        "uvicorn", 
        "runner_service:app", 
        "--host", 
        "0.0.0.0", 
        "--port",
        "8001",
        app=app,
        image=agent_sdk_env.image,
        secrets=agent_sdk_env.secrets,
        workdir=agent_sdk_env.workdir,
        encrypted_ports=[8001],
        timeout=60 * 60 * 6, # 6 hours
        idle_timeout=60 * 10, # 10 minutes idle shutdown
        verbose=True,
    )

    SERVICE_URL = None
    # Give the tunnel a moment to provision; then find the encrypted URL for 8001
    deadline = time.time() + 30
    while time.time() < deadline:
        tunnels = SANDBOX.tunnels()
        if 8001 in tunnels and getattr(tunnels[8001], "url", None):
            SERVICE_URL = tunnels[8001].url
            break
        time.sleep(0.5)

    if SERVICE_URL:
        _wait_for_service(SERVICE_URL)
        return SANDBOX, SERVICE_URL

    raise RuntimeError("Failed to start background sandbox or get service URL")



@app.function(
    image=agent_sdk_env.image,
    secrets=agent_sdk_env.secrets,
)
# You can also turn one into an HTTP endpoint if needed
# Requires FastAPI to be installed in the sandbox image
# @modal.fastapi_endpoint(docs=True) 
def run_agent_remote(question: str =  DEFAULT_QUESTION) -> None:
    """Run the agent once in a short-lived Modal function.

    This is useful for synchronous, on-demand runs. For long-running, low-latency
    serving, prefer the background sandbox pattern used by `test_endpoint`.

    Args:
        question: Natural-language query to send to the agent.
    """
    import anyio
    from runner import run_agent
    anyio.run(run_agent, question)


@app.function(
    image=agent_sdk_env.image,
    secrets=agent_sdk_env.secrets,
    timeout=300,
    # schedule=modal.Cron("*/10 * * * *"), # Run every 10 minutes
)
@modal.fastapi_endpoint(method="POST")
async def test_endpoint(request: Request) -> Response:
    """HTTP endpoint that proxies a query to the background sandbox service.

    The background service is a FastAPI app defined in `runner_service.py` and
    hosted inside a long-lived `modal.Sandbox`. We resolve or start that
    sandbox, then POST the incoming JSON body to `/query` and stream back the
    result.

    Returns:
        A JSON `Response` with either the agent result or an error status.

    CURL example (dev deployment):

        ```bash
        curl -X POST 'https://<org>--test-sandbox-test-endpoint-dev.modal.run' \
          -H 'Content-Type: application/json' \
          -d '"What is the capital of Canada?"'
        ```

    Notes:
        - The request body for this endpoint should be a JSON string (not an
          object). The function wraps it as `{ "question": <string> }` for the
          internal service at `/query`.
        - Before invoking the curl example, start the dev server with
          `modal serve main.py` (or deploy with `modal deploy main.py`).
    """
    question = await request.json()
    _, url = get_or_start_background_sandbox()

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
            r = await client.post(f"{url.rstrip('/')}/query", json={"question": question})
            r.raise_for_status()
            data = r.json()
        return Response(content=json.dumps(data), media_type="application/json", status_code=200)
    except httpx.TimeoutException:
        return Response(
            content=json.dumps({"error": "Upstream timed out contacting the sandbox service"}),
            media_type="application/json",
            status_code=504,
        )
    except httpx.HTTPError as e:
        return Response(
            content=json.dumps({"error": f"Upstream HTTP error: {str(e)}"}),
            media_type="application/json",
            status_code=502,
        )            


# @app.function(
#     image=agent_sdk_env.image
# )
# @modal.fastapi_endpoint()
# def square(x: int):
#     return {"square": x**2}


@app.function(
    image=agent_sdk_env.image,
    secrets=agent_sdk_env.secrets,
    schedule=modal.Cron("*/5 * * * *"), # Run every 5 minutes
)
def sandbox_controller(question: str = DEFAULT_QUESTION) -> None:
    """Periodic job that spins up a sandbox and executes `runner.py` inside it.

    This demonstrates driving a sandbox with a command (here `python runner.py`)
    and capturing its stdout/stderr. Useful for batch or scheduled workflows.

    Args:
        question: Passed through to `runner.py` via `--question`.
    """
    import modal
    sb = modal.Sandbox.create(
        app=app,
        image=agent_sdk_env.image,
        secrets=agent_sdk_env.secrets,
        workdir=agent_sdk_env.workdir,
        timeout=60 * 10, # 10 minutes
    )
    print("\n=== EXECUTING RUNNER ===")
    p = sb.exec("python", "runner.py", "--question", question, timeout=60)
    print("=== STDOUT ===")
    for line in p.stdout:
        print(line, end="")
    print("\n=== STDERR ===")
    for line in p.stderr:
        print(line, end="")

    sb.terminate()



# For 'modal run' command
@app.local_entrypoint()
def main():
    """Local entry point for `modal run main.py` during development.

    Spins up a short-lived sandbox, executes `runner.py`, streams logs, and
    terminates the sandbox. Prefer `modal serve main.py` to keep endpoints and
    hot code reloading during development.
    """
    sb = modal.Sandbox.create(
        app=app,
        image=agent_sdk_env.image,
        secrets=agent_sdk_env.secrets,
        workdir=agent_sdk_env.workdir,
        timeout=60 * 10, # 10 minutes
        verbose=True,
    )

    p = sb.exec("python", "runner.py", timeout=60)

    print("=== STDOUT ===")
    for line in p.stdout:
        print(line, end="")
    print("\n=== STDERR ===")
    for line in p.stderr:
        print(line, end="")

    sb.terminate()

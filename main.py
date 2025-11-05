"""
Entry-point and Modal function definitions for running the agent in a sandboxed
environment and exposing lightweight HTTP endpoints.

Quickstart (CLI):
- run local_entrypoint: `modal run main.py` (runs the agent once in a short-lived Modal function)
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
import anyio
import logging
from utils.schemas import QueryBody
from starlette.responses import StreamingResponse
from modal import exception as modal_exc

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

@app.function(image=agent_sdk_env.image, secrets=agent_sdk_env.secrets)
@modal.asgi_app()
def http_app():
    return web_app

@web_app.get("/health")
async def health():
    return {"ok": True}

@web_app.post("/query")
async def query_proxy(request: Request, body: QueryBody):
    # Use async getter to avoid blocking event loop
    sb, url = await get_or_start_background_sandbox_aio()

    # Optional: per-request connect token (verified in sandbox service)
    headers = {}
    if ENFORCE_CONNECT_TOKEN:
        creds = await sb.create_connect_token.aio(user_metadata={"ip": request.client.host or "unknown"})
        headers = {"Authorization": f"Bearer {creds.token}"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        r = await client.post(
            f"{url.rstrip('/')}/query", 
            json=body.model_dump(), 
            headers=headers,
            timeout=httpx.Timeout(120.0, connect=30.0)
        )
        r.raise_for_status()
        return r.json()

@web_app.post("/query_stream")
async def query_stream(request: Request, body: QueryBody):
    sb, url = await get_or_start_background_sandbox_aio()
    
    headers = {}
    if ENFORCE_CONNECT_TOKEN:
        creds = await sb.create_connect_token.aio(user_metadata={"ip": request.client.host or "unknown"})
        headers = {"Authorization": f"Bearer {creds.token}"}

    async def sse_proxy():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", 
                f"{url.rstrip('/')}/query_stream", 
                json=body.model_dump(), 
                headers=headers
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk
    return StreamingResponse(
        sse_proxy(), 
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"}
    )

# Add endpoints to fetch last N logs and current tunnel URL, plus a lightweight ping proxy
@web_app.get("/service_info")
async def service_info():
    sb, url = await get_or_start_background_sandbox_aio()
    return {
        "url": url,
        "sandbox_id": sb.object_id
    }


@app.function(image=agent_sdk_env.image, secrets=agent_sdk_env.secrets)
async def tail_logs(n: int = 200, timeout: float = 2.0) -> list[str]:
    sb, _ = await get_or_start_background_sandbox_aio()
    from collections import deque
    buf = deque(maxlen=n)
    async with anyio.move_on_after(timeout):
        async for msg in sb.stdout.aio():
            for line in str(msg).splitlines():
                buf.append(line)
    return list(buf)

# Persistent registry for sandbox metadata (survives sandbox restarts)
SESSIONS = modal.Dict.from_name("sandbox-sessions", create_if_missing=True)

# Service sandbox identity and config
SANDBOX_NAME = "svc-runner-8001"
SERVICE_PORT = 8001
PERSIST_VOL_NAME = f"{SANDBOX_NAME}-vol"


# Toggle optional auth with connect tokens
ENFORCE_CONNECT_TOKEN = False

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
    delay = 0.5
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(check_url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, socket.timeout):
            time.sleep(delay)
            delay = min(delay * 1.5, 3.0)
    raise TimeoutError(f"Service {check_url} did not become available within {timeout} seconds")


# A cron cleanup that reaps stale records in SESSIONS and restarts a dead sandbox
@app.function(image=agent_sdk_env.image, secrets=agent_sdk_env.secrets, schedule=modal.Cron("*/2 * * * *"))
def cleanup_sessions():
    try:
        sb = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)
        _ = sb.tunnels()  # Will raise if gone
        SESSIONS[SANDBOX_NAME] = {**SESSIONS.get(SANDBOX_NAME, {}), "status": "running"}
    except modal_exc.NotFoundError:
        SESSIONS[SANDBOX_NAME] = {"status": "missing"}
    except Exception:
        logging.getLogger(__name__).exception("Unexpected error cleaning up sessions")


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
    
    # Attempt global reuse by name across workers/processes
    try:
        sb = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)
        tunnels = sb.tunnels()
        if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
            SANDBOX = sb
            SERVICE_URL = tunnels[SERVICE_PORT].url
            _wait_for_service(SERVICE_URL)
            return SANDBOX, SERVICE_URL
    except Exception as e:
        pass

    svc_vol = modal.Volume.from_name(PERSIST_VOL_NAME, create_if_missing=True)
    SANDBOX = modal.Sandbox.create(
        # Command to run persistently in the background
        "uvicorn", 
        "runner_service:app", 
        "--host", 
        "0.0.0.0", 
        "--port",
        str(SERVICE_PORT),
        app=app,
        image=agent_sdk_env.image,
        secrets=agent_sdk_env.secrets,
        workdir=agent_sdk_env.workdir,
        name=SANDBOX_NAME,
        # tags={"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)},
        encrypted_ports=[SERVICE_PORT],
        volumes={"/data": svc_vol},
        timeout=60 * 60 * 12, # 12 hours
        idle_timeout=60 * 10, # 10 minutes idle shutdown
        cpu=1.0,              # vCPU
        memory=2048,          # MB
        verbose=True,
        # block_network=False,   # Allow all network access
        # cidr_allowlist=["1.2.3.4/32"], # Allow specific IP ranges
    )

    # Optional: set tags after creation
    SANDBOX.set_tags({"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)})

    SERVICE_URL = None
    # Give the tunnel a moment to provision; then find the encrypted URL for 8001
    deadline = time.time() + 30
    while time.time() < deadline:
        tunnels = SANDBOX.tunnels()
        if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
            SERVICE_URL = tunnels[SERVICE_PORT].url
            break
        time.sleep(0.5)

    if not SERVICE_URL:
        raise RuntimeError("Failed to start background sandbox or get service URL")

    _wait_for_service(SERVICE_URL)
    try:
        SESSIONS[SANDBOX_NAME] = {
            "id": SANDBOX.object_id,
            "url": SERVICE_URL,
            "volume": PERSIST_VOL_NAME,
            "created_at": int(time.time()),
            "tags": {"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)},
            "status": "running",
        }
    except modal_exc.Error as e:
        logging.getLogger(__name__).warning(
            "Failed to persist session metadata to Modal Dict: %s", e
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Unexpected error persisting session metadata"
        )

    return SANDBOX, SERVICE_URL



async def _wait_for_service_aio(url: str, timeout: int = 60, path: str = "/health_check") -> None:
    check_url = f"{url.rstrip('/')}{path}"
    deadline = anyio.current_time() + timeout
    async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=2.0)) as client:
        while anyio.current_time() < deadline:
            try:
                r = await client.get(check_url)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            await anyio.sleep(1)
    raise TimeoutError(f"Service {check_url} did not become available within {timeout} seconds")


async def get_or_start_background_sandbox_aio() -> tuple[modal.Sandbox, str]:
    global SANDBOX, SERVICE_URL

    if SANDBOX and SERVICE_URL:
        return SANDBOX, SERVICE_URL

    # Attempt global reuse by name across workers/processes
    try:
        sb = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)
        # Poll tunnels until URL appears (mirrors sync behavior)
        deadline = anyio.current_time() + 30
        url = None
        while anyio.current_time() < deadline:
            tunnels = await sb.tunnels.aio()
            if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
                url = tunnels[SERVICE_PORT].url
                break
            await anyio.sleep(0.5)
        if url:
            SANDBOX, SERVICE_URL = sb, url
            await _wait_for_service_aio(SERVICE_URL)
            return SANDBOX, SERVICE_URL
    except Exception:
        pass

    # Create with persistent volume
    svc_vol = modal.Volume.from_name(PERSIST_VOL_NAME, create_if_missing=True)
    SANDBOX = await modal.Sandbox.create.aio(
        "uvicorn", "runner_service:app", "--host", "0.0.0.0", "--port", str(SERVICE_PORT),
        app=app,
        image=agent_sdk_env.image,
        secrets=agent_sdk_env.secrets,
        workdir=agent_sdk_env.workdir,
        name=SANDBOX_NAME,
        # tags={"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)},
        encrypted_ports=[SERVICE_PORT],
        volumes={"/data": svc_vol},
        timeout=60 * 60 * 12,
        idle_timeout=60 * 10,
        cpu=1.0,
        memory=2048,
        verbose=True,
    )

    # Optional: set tags after creation
    await SANDBOX.set_tags.aio({"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)})


    # Poll tunnels until URL appears
    deadline = anyio.current_time() + 30
    SERVICE_URL = None
    while anyio.current_time() < deadline:
        tunnels = await SANDBOX.tunnels.aio()
        if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
            SERVICE_URL = tunnels[SERVICE_PORT].url
            break
        await anyio.sleep(0.5)

    if not SERVICE_URL:
        raise RuntimeError("Failed to start background sandbox or get service URL")

    await _wait_for_service_aio(SERVICE_URL)

    # Persist session metadata
    try:
        SESSIONS[SANDBOX_NAME] = {
            "id": SANDBOX.object_id,
            "url": SERVICE_URL,
            "volume": PERSIST_VOL_NAME,
            "created_at": int(time.time()),
            "tags": {"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)},
            "status": "running",
        }
    except Exception: #TODO: Better error handling
        pass

    return SANDBOX, SERVICE_URL

@app.function(
    image=agent_sdk_env.image,
    secrets=agent_sdk_env.secrets,
    volumes={"/data": modal.Volume.from_name(PERSIST_VOL_NAME, create_if_missing=True)},
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


# @app.function(
#     image=agent_sdk_env.image,
#     secrets=agent_sdk_env.secrets,
#     timeout=300,
#     volumes={"/data": modal.Volume.from_name(PERSIST_VOL_NAME, create_if_missing=True)},
#     # schedule=modal.Cron("*/10 * * * *"), # Run every 10 minutes
# )
# @modal.fastapi_endpoint(method="POST")
# async def test_endpoint(request: Request) -> Response:
#     """HTTP endpoint that proxies a query to the background sandbox service.

#     The background service is a FastAPI app defined in `runner_service.py` and
#     hosted inside a long-lived `modal.Sandbox`. We resolve or start that
#     sandbox, then POST the incoming JSON body to `/query` and stream back the
#     result.

#     Returns:
#         A JSON `Response` with either the agent result or an error status.

#     CURL example (dev deployment):

#         ```bash
#         curl -X POST 'https://<org>--test-sandbox-test-endpoint-dev.modal.run' \
#           -H 'Content-Type: application/json' \
#           -d '"What is the capital of Canada?"'
#         ```

#     Notes:
#         - The request body for this endpoint should be a JSON string (not an
#           object). The function wraps it as `{ "question": <string> }` for the
#           internal service at `/query`.
#         - Before invoking the curl example, start the dev server with
#           `modal serve main.py` (or deploy with `modal deploy main.py`).
#     """
#     question = await request.json()
#     # sb, url = get_or_start_background_sandbox()
#     sb, url = await get_or_start_background_sandbox_aio()
#     # Optional: per-request connect token (verified in sandbox service)
#     headers = {}
#     if ENFORCE_CONNECT_TOKEN:
#         creds = await sb.create_connect_token.aio(user_metadata={"ip": request.client.host or "unknown"})
#         headers = {"Authorization": f"Bearer {creds.token}"}

#     try:
#         async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
#             r = await client.post(f"{url.rstrip('/')}/query", json={"question": question}, headers=headers)
#             r.raise_for_status()
#             data = r.json()
#         return Response(content=json.dumps(data), media_type="application/json", status_code=200)
#     except httpx.TimeoutException:
#         return Response(
#             content=json.dumps({"error": "Upstream timed out contacting the sandbox service"}),
#             media_type="application/json",
#             status_code=504,
#         )
#     except httpx.HTTPError as e:
#         return Response(
#             content=json.dumps({"error": f"Upstream HTTP error: {str(e)}"}),
#             media_type="application/json",
#             status_code=502,
#         )            

@app.function(
    image=agent_sdk_env.image,
    secrets=agent_sdk_env.secrets,
)
def terminate_service_sandbox() -> dict:
    """Terminate the background sandbox to flush writes to the volume.
    
    Sandbox writes are only synced to the volume when the sandbox terminates.
    Call this function after the agent has created files to ensure they are persisted.
    
    Returns:
        Dict with termination status
    """
    global SANDBOX
    try:
        sb, _ = get_or_start_background_sandbox()
        sb.terminate()
        SANDBOX = None  # Clear global so a new one will be created on next request
        return {"ok": True, "message": "Sandbox terminated, writes flushed to volume"}
    except modal_exc.NotFoundError as e:
        return {"ok": False, "error": "Sandbox not found", "detail": str(e), "type": "NotFoundError"}
    except modal_exc.SandboxTerminatedError:
        return {"ok": False, "error": "Sandbox already terminated", "type": "SandboxTerminatedError"}
    except modal_exc.TimeoutError as e:
        return {"ok": False, "error": "Sandbox termination timed out", "type": e.__class__.__name__}
    except modal_exc.Error as e:
        return {"ok": False, "error": str(e), "type": e.__class__.__name__}
    except Exception:
        logging.getLogger(__name__).exception("Unexpected error terminating sandbox")
        return {"ok": False, "error": "Unexpected error", "type": "UnexpectedException"}


# Snapshot function to capture filesystem diffs and store snapshot metadata
@app.function(image=agent_sdk_env.image, secrets=agent_sdk_env.secrets, timeout=300)
def snapshot_service() -> dict:
    sb, _ = get_or_start_background_sandbox()
    img = sb.snapshot_filesystem()
    info = {"image_id": img.object_id, "ts": int(time.time()), "base": SANDBOX_NAME}
    try:
        SESSIONS[f"{SANDBOX_NAME}-snapshot"] = info
    except modal_exc.Error as e:
        logging.getLogger(__name__).warning(
            "Failed to persist snapshot metadata to Modal Dict: %s", e
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "Unexpected error persisting snapshot metadata"
        )
    return info

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

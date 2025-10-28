"""
Example usage:
run local_entrypoint: modal run main.py
run sandbox_controller: modal run main.py::sandbox_controller --question "What is the capital of France?"
run run_agent_remote: modal run main.py::run_agent_remote --question "What is the capital of France?"
keep dev deployment running: modal serve main.py
deploy to production: modal deploy main.py
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
SANDBOX: Optional[modal.Sandbox] = None
SERVICE_URL: Optional[str] = None

def _wait_for_service(url: str, timeout: int = 60, path: str = "/health_check") -> None:
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

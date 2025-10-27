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
    # schedule=modal.Cron("*/10 * * * *"), # Run every 10 minutes
)
@modal.fastapi_endpoint(method="POST")
async def test_endpoint(request: Request) -> Response:
    """
    To test this endpoint using curl, run:

    curl -X POST http://localhost:8000/test_endpoint \
        -H 'Content-Type: application/json' \
        -d '"What is the capital of France?"'

    (If running in Modal's local dev server, replace the URL if needed.)

    The body should be a JSON-encoded string, e.g. "\"Your question here\""
    """
    question = await request.json()
    print(question)
    sb = modal.Sandbox.create(
        app=app,
        image=agent_sdk_env.image,
        secrets=agent_sdk_env.secrets,
        workdir=agent_sdk_env.workdir,
        timeout=60 * 10, # 10 minutes
    )
    print("=== STDOUT ===")
    p = sb.exec("python", "runner.py", "--question", question, timeout=60)
    
    # Read the stdout content from the StreamReader
    stdout_content = "".join(p.stdout)
    
    return Response(content=stdout_content, status_code=200)



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

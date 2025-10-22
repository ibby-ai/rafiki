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

app = modal.App("test-sandbox")

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
    schedule=modal.Cron("*/10 * * * *"), # Run every 10 minutes
    image=agent_sdk_env.image,
    secrets=agent_sdk_env.secrets,
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
    try:
        print("\n=== EXECUTING RUNNER ===")
        p = sb.exec("python", "runner.py", "--question", question, timeout=60)
        print("=== STDOUT ===")
        for line in p.stdout:
            print(line, end="")
        print("\n=== STDERR ===")
        for line in p.stderr:
            print(line, end="")
    finally:
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

import modal
from utils.env_templates import get_env_template

app = modal.App("test-sandbox")

agent_sdk_env = get_env_template("base-anthropic-sdk")

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

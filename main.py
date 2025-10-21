import modal


app = modal.App("test-sandbox")

image = (
    modal.Image.debian_slim(python_version="3.11").pip_install("claude-agent-sdk")
    .apt_install("curl")
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
        "apt-get install -y nodejs",
        "npm install -g @anthropic-ai/claude-code", # Needed for Agent SDK
    )
    .workdir("/root/app") # Declare the working directory for the sandbox.
    .add_local_dir(".", remote_path="/root/app")
)


@app.local_entrypoint()
def main():
    sb = modal.Sandbox.create(
        app=app,
        image=image,
        secrets=[modal.Secret.from_name("anthropic-secret", required_keys=["ANTHROPIC_API_KEY"])],
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

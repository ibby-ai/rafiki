import modal


app = modal.App.lookup("test-sandbox", create_if_missing=True)

sb = modal.Sandbox.create(
    name="test-sandbox",
    app=app,
    # This image is the environment in which the sandbox will be run.
    # This image provides us with a linux environment / computer for the sandbox to run on.
    # So have access to the filesystem and all the tools and libraries that are installed in the image.
    image= (
        modal.Image.debian_slim(python_version="3.11").pip_install("claude-agent-sdk")
        .apt_install("curl")
        .run_commands(
            "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
            "apt-get install -y nodejs",
            "npm install -g @anthropic-ai/claude-code", # Needed for Agent SDK
        )
        .workdir("/root/app") # Declare the working directory for the sandbox.
        .add_local_dir(".", remote_path="/root/app")
    ),
    secrets=[modal.Secret.from_name("anthropic-secret", required_keys=["ANTHROPIC_API_KEY"])],
    timeout=60 * 10, # 10 minutes
    verbose=True,
)

p = sb.exec("python", "-c", "print('hello')", timeout=3)
print(p.stdout.read())


# Print current working directory
print(sb.exec("pwd").stdout.read())
# Print available files in the working directory
print(sb.exec("ls", "-la").stdout.read())

# Run the runner.py file
p = sb.exec("python", "runner.py", timeout=60 * 10)
# print(p.stdout.read())
print("=== STDOUT ===")
for line in p.stdout:
    print(line, end="")
print("\n=== STDERR ===")
for line in p.stderr:
    print(line, end="")


p = sb.exec("bash", "-c", "for i in {1..10}; do date +%T; sleep 0.5; done", timeout=5)
for line in p.stdout:
    # Avoid double newlines by using end="".
    print(line, end="")

sb.terminate()
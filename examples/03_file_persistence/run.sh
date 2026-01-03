#!/bin/bash
# File Persistence Example
# Demonstrates how files written to /data/ persist to Modal Volume

set -e

VOLUME_NAME="svc-runner-8001-vol"
COMMIT_WAIT_SECONDS="${COMMIT_WAIT_SECONDS:-65}"

echo "=== File Persistence Example ==="
echo ""

# Step 1: Ask the agent to create a file
echo "1. Asking agent to create a Python file..."
uv run modal run -m agent_sandbox.app::run_agent_remote \
    --question "Create a simple Python file at /data/hello_example.py that prints 'Hello from the sandbox!'"

echo ""

# Step 2: Flush writes via commit interval or termination
if [ "${SKIP_TERMINATE}" = "true" ]; then
    echo "2. Waiting ${COMMIT_WAIT_SECONDS}s for volume commit interval..."
    sleep "${COMMIT_WAIT_SECONDS}"
else
    echo "2. Terminating sandbox to flush writes to volume..."
    uv run modal run -m agent_sandbox.app::terminate_service_sandbox
fi

echo ""

# Step 3: List files in volume
echo "3. Listing files in volume '${VOLUME_NAME}':"
uv run modal volume ls "${VOLUME_NAME}"

echo ""

# Step 4: Download and display the file
echo "4. Downloading and displaying the file:"
uv run modal volume get "${VOLUME_NAME}" /hello_example.py /tmp/hello_example.py
echo "--- File contents ---"
cat /tmp/hello_example.py
echo "--- End of file ---"

echo ""

# Step 5: Validate Python syntax
echo "5. Validating Python syntax:"
python3 -m py_compile /tmp/hello_example.py && echo "Valid Python syntax!"

echo ""
echo "=== Example Complete ==="
echo ""
echo "Clean up: modal volume rm ${VOLUME_NAME} /hello_example.py"

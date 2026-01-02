#!/bin/bash
# Service Management Example
# Demonstrates sandbox lifecycle management

set -e

VOLUME_NAME="svc-runner-8001-vol"

echo "=== Service Management Example ==="
echo ""

# Show current volume state
echo "1. Current volume contents:"
modal volume ls "${VOLUME_NAME}" || echo "   (volume empty or not found)"
echo ""

# Terminate service sandbox
echo "2. Terminating service sandbox (flushes writes to volume):"
modal run -m agent_sandbox.app::terminate_service_sandbox
echo ""

# Create snapshot
echo "3. Creating filesystem snapshot:"
modal run -m agent_sandbox.app::snapshot_service
echo ""

# Show volume state again
echo "4. Volume contents after operations:"
modal volume ls "${VOLUME_NAME}" || echo "   (volume empty)"
echo ""

echo "=== Service Management Complete ==="
echo ""
echo "Next request will start a fresh sandbox."

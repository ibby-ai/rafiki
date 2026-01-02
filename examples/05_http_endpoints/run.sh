#!/bin/bash
# HTTP Endpoints Example
# Demonstrates all available HTTP endpoints

set -e

DEV_URL="${DEV_URL:-https://your-org--test-sandbox-http-app-dev.modal.run}"

echo "=== HTTP Endpoints Example ==="
echo "Using endpoint: ${DEV_URL}"
echo ""
echo "NOTE: Ensure 'modal serve -m agent_sandbox.app' is running first!"
echo ""

# Health check
echo "1. Health Check (GET /health):"
curl -s "${DEV_URL}/health" | python3 -m json.tool
echo ""

# Service info
echo "2. Service Info (GET /service_info):"
curl -s "${DEV_URL}/service_info" | python3 -m json.tool
echo ""

# Query endpoint
echo "3. Query (POST /query):"
curl -s -X POST "${DEV_URL}/query" \
    -H 'Content-Type: application/json' \
    -d '{"question":"What is 2 + 2?"}' | python3 -m json.tool
echo ""

# Streaming (limited output)
echo "4. Streaming Query (POST /query_stream) - first 5 seconds:"
timeout 5 curl -N -X POST "${DEV_URL}/query_stream" \
    -H 'Content-Type: application/json' \
    -d '{"question":"Count from 1 to 3"}' || true
echo ""

echo ""
echo "=== Example Complete ==="

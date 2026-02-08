#!/bin/bash
# Streaming Responses Example
# Demonstrates SSE streaming via /query_stream

set -e

DEV_URL="${DEV_URL:-https://your-org--test-sandbox-http-app-dev.modal.run}"

echo "=== Streaming Responses Example ==="
echo "Using endpoint: ${DEV_URL}"
echo ""
echo "NOTE: Ensure 'modal serve -m modal_backend.main' is running first!"
echo ""

# Stream a response
echo "Streaming response (press Ctrl+C to stop):"
curl -N -X POST "${DEV_URL}/query_stream" \
    -H 'Content-Type: application/json' \
    -d '{"question":"Explain the differences between Python lists and tuples in 3 bullet points"}'

echo ""
echo "=== Example Complete ==="

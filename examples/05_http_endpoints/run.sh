#!/bin/bash
# HTTP Endpoints Example
# Demonstrates all available HTTP endpoints

set -e

DEV_URL="${DEV_URL:-https://your-org--test-sandbox-http-app-dev.modal.run}"
MODAL_PROXY_KEY="${MODAL_PROXY_KEY:-}"
MODAL_PROXY_SECRET="${MODAL_PROXY_SECRET:-}"

EXTRA_HEADERS=()
if [ -n "${MODAL_PROXY_KEY}" ] && [ -n "${MODAL_PROXY_SECRET}" ]; then
    EXTRA_HEADERS+=(-H "Modal-Key: ${MODAL_PROXY_KEY}" -H "Modal-Secret: ${MODAL_PROXY_SECRET}")
elif [ -n "${MODAL_PROXY_KEY}" ] || [ -n "${MODAL_PROXY_SECRET}" ]; then
    echo "Warning: set both MODAL_PROXY_KEY and MODAL_PROXY_SECRET to use Proxy Auth."
fi

echo "=== HTTP Endpoints Example ==="
echo "Using endpoint: ${DEV_URL}"
echo ""
echo "NOTE: Ensure 'modal serve -m modal_backend.main' is running first!"
echo ""

# Health check
echo "1. Health Check (GET /health):"
curl -s "${EXTRA_HEADERS[@]}" "${DEV_URL}/health" | python3 -m json.tool
echo ""

# Service info
echo "2. Service Info (GET /service_info):"
curl -s "${EXTRA_HEADERS[@]}" "${DEV_URL}/service_info" | python3 -m json.tool
echo ""

# Query endpoint
echo "3. Query (POST /query):"
curl -s -X POST "${DEV_URL}/query" \
    -H 'Content-Type: application/json' \
    "${EXTRA_HEADERS[@]}" \
    -d '{"question":"What is 2 + 2?"}' | python3 -m json.tool
echo ""

# Streaming (limited output)
echo "4. Streaming Query (POST /query_stream) - first 5 seconds:"
# Use gtimeout (macOS coreutils) or timeout (Linux), with fallback
TIMEOUT_CMD=""
if command -v gtimeout &> /dev/null; then
    TIMEOUT_CMD="gtimeout 5"
elif command -v timeout &> /dev/null; then
    TIMEOUT_CMD="timeout 5"
fi

if [ -n "$TIMEOUT_CMD" ]; then
    $TIMEOUT_CMD curl -N -X POST "${DEV_URL}/query_stream" \
        -H 'Content-Type: application/json' \
        "${EXTRA_HEADERS[@]}" \
        -d '{"question":"Count from 1 to 3"}' || true
else
    echo "(timeout not available - streaming for ~3 seconds using background process)"
    curl -N -X POST "${DEV_URL}/query_stream" \
        -H 'Content-Type: application/json' \
        "${EXTRA_HEADERS[@]}" \
        -d '{"question":"Count from 1 to 3"}' &
    CURL_PID=$!
    sleep 3
    kill $CURL_PID 2>/dev/null || true
    wait $CURL_PID 2>/dev/null || true
fi
echo ""

echo ""
echo "=== Example Complete ==="

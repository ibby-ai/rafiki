#!/bin/bash
# Batch Processing Example
# Demonstrates processing multiple queries sequentially

set -e

echo "=== Batch Processing Example ==="
echo ""

QUESTIONS=(
    "What is the capital of France?"
    "What is the capital of Germany?"
    "What is the capital of Japan?"
)

echo "Processing ${#QUESTIONS[@]} queries..."
echo ""

for i in "${!QUESTIONS[@]}"; do
    echo "=== Query $((i+1))/${#QUESTIONS[@]} ==="
    echo "Q: ${QUESTIONS[$i]}"
    echo ""

    uv run modal run -m modal_backend.main::run_agent_remote \
        --question "${QUESTIONS[$i]}" 2>&1 | tail -5

    echo ""
done

echo "=== Batch Processing Complete ==="

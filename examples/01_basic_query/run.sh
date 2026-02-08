#!/bin/bash
# Basic Query Example
# Demonstrates the simplest way to run an agent query

set -e

echo "=== Basic Query Example ==="
echo ""

# Simple factual question
echo "1. Simple factual question:"
uv run modal run -m modal_backend.main::run_agent_remote \
    --question "What is the capital of France?"

echo ""
echo "=== Example Complete ==="

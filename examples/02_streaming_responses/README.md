# Streaming Responses

Demonstrates Server-Sent Events (SSE) streaming for real-time agent responses.

## Prerequisites

Start the dev server in a separate terminal:

```bash
uv run modal serve -m modal_backend.main
```

Note the URL printed (e.g., `https://your-org--modal-backend-http-app-dev.modal.run`)

## Usage

```bash
# Set your dev URL
export DEV_URL="https://your-org--modal-backend-http-app-dev.modal.run"

./run.sh
```

## How It Works

The `/query_stream` endpoint returns Server-Sent Events (SSE) that stream the agent's response in real-time. Each event contains a JSON payload with the response chunk.

## SSE Event Format

```
data: {"type": "assistant", "content": "..."}
data: {"type": "tool_use", "name": "...", "input": {...}}
data: {"type": "result", "result": "..."}
```

## When to Use This Pattern

- Interactive chat applications
- Real-time UI updates
- Long-running queries where you want incremental feedback

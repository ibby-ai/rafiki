# HTTP Endpoints

Comprehensive guide to all HTTP endpoints exposed by the agent service.

## Prerequisites

Start the dev server:

```bash
uv run modal serve -m agent_sandbox.app
```

Set the URL:

```bash
export DEV_URL="https://your-org--test-sandbox-http-app-dev.modal.run"
```

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/query` | POST | Send a query, get full response |
| `/query_stream` | POST | Send a query, stream response via SSE |
| `/service_info` | GET | Get sandbox URL and ID |

## Usage

### Shell Script

```bash
./run.sh
```

### Python Client

```bash
uv run python client.py $DEV_URL
```

## Request/Response Format

### POST /query

Request:
```json
{
  "question": "What is 2 + 2?"
}
```

Response:
```json
{
  "summary": {
    "text": "The answer is 4."
  },
  "messages": [...],
  "usage": {...}
}
```

### POST /query_stream

Request: Same as `/query`

Response: Server-Sent Events stream
```
data: {"type": "assistant", "content": "..."}
data: {"type": "result", "result": "..."}
```

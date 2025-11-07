# API Usage Guide: How Users Interact with Endpoints

This guide explains how end users interact with your deployed agent sandbox application, including all available endpoints, request/response formats, authentication, and real-world usage examples.

## Table of Contents

- [Deployment and Public URLs](#deployment-and-public-urls)
- [Available Endpoints](#available-endpoints)
- [Real-World Usage Examples](#real-world-usage-examples)
- [Authentication](#authentication)
- [Error Handling](#error-handling)
- [Production Considerations](#production-considerations)

## Deployment and Public URLs

### Deploying the Application

To deploy your application to production:

```bash
modal deploy -m agent_sandbox.deploy
```

After deployment, Modal automatically provides a public HTTPS URL.

### URL Format

The public URL follows this pattern:

```
https://<your-org>--test-sandbox-http-app.modal.run
```

**Components:**
- `<your-org>`: Your Modal organization name (e.g., `acme-corp`)
- `test-sandbox`: The app name (from `modal.App("test-sandbox")`)
- `http-app`: The function name (from `@modal.asgi_app()`)

**Example:**
```
https://acme-corp--test-sandbox-http-app.modal.run
```

### Finding Your URL

You can find your deployment URL in several ways:

1. **Modal Dashboard**: After deployment, check the dashboard for your app
2. **Terminal Output**: The URL is displayed after `modal deploy` completes
3. **Modal CLI**: Run `modal app list` to see all deployed apps and their URLs

### Development vs Production URLs

**Development** (when using `modal serve`):
```
https://<org>--test-sandbox-http-app-dev.modal.run
```

**Production** (when using `modal deploy`):
```
https://<org>--test-sandbox-http-app.modal.run
```

Note the `-dev` suffix in development URLs.

## Available Endpoints

### 1. GET /health - Health Check

**Purpose:** Verify the service is running and accessible

**Request:**
```bash
curl https://acme-corp--test-sandbox-http-app.modal.run/health
```

**Response:**
```json
{
  "ok": true
}
```

**Status Codes:**
- `200 OK`: Service is healthy

**Use Cases:**
- Monitoring and uptime checks
- Load balancer health checks
- Quick verification that the service is live
- Integration testing

**Example Usage:**
```bash
# Simple health check
curl https://your-url.modal.run/health

# With verbose output
curl -v https://your-url.modal.run/health

# Check response time
time curl -s https://your-url.modal.run/health
```

---

### 2. POST /query - Execute Agent Query (Non-Streaming)

**Purpose:** Send a question to the agent and receive a complete response

**Endpoint:** `POST /query`

**Request Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
  "question": "Your question here"
}
```

**Request Example:**
```bash
curl -X POST https://acme-corp--test-sandbox-http-app.modal.run/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the capital of Canada?"}'
```

**Response:**
```json
{
  "ok": true,
  "messages": [
    "The capital of Canada is Ottawa.",
    "Ottawa is located in the province of Ontario..."
  ]
}
```

**Response Fields:**
- `ok` (boolean): Indicates success
- `messages` (array of strings): Agent response messages

**Status Codes:**
- `200 OK`: Query executed successfully
- `400 Bad Request`: Invalid request body (missing `question` field)
- `401 Unauthorized`: Missing or invalid authentication token
- `500 Internal Server Error`: Agent error or sandbox issue
- `503 Service Unavailable`: Sandbox not ready (typically on first request)

**Characteristics:**
- **Timeout:** 120 seconds
- **Response Type:** Complete response (all messages at once)
- **Best For:** Simple questions, synchronous workflows, when you need the full response before proceeding

**Example with Error Handling:**
```bash
curl -X POST https://your-url.modal.run/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Explain Python"}' \
  -w "\nHTTP Status: %{http_code}\n"
```

---

### 3. POST /query_stream - Execute Agent Query (Streaming)

**Purpose:** Stream agent responses in real-time via Server-Sent Events (SSE)

**Endpoint:** `POST /query_stream`

**Request Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
  "question": "Your question here"
}
```

**Request Example:**
```bash
curl -X POST https://acme-corp--test-sandbox-http-app.modal.run/query_stream \
  -H "Content-Type: application/json" \
  -d '{"question": "Explain quantum computing in detail"}' \
  --no-buffer
```

**Response:** Server-Sent Events (SSE) stream
```
data: Quantum computing is a type of computation...

data: that uses quantum mechanical phenomena...

data: such as superposition and entanglement...

event: done
data: {}
```

**Response Format:**
- Each message chunk is prefixed with `data: `
- Empty line (`\n\n`) separates events
- Final event: `event: done\ndata: {}\n\n`

**Status Codes:**
- `200 OK`: Stream started successfully
- `400 Bad Request`: Invalid request body
- `401 Unauthorized`: Missing or invalid authentication token
- `500 Internal Server Error`: Agent error
- `503 Service Unavailable`: Sandbox not ready

**Characteristics:**
- **Timeout:** None (streams until complete)
- **Response Type:** Server-Sent Events (SSE)
- **Content-Type:** `text/event-stream`
- **Best For:** Long-form answers, interactive UIs, real-time feedback, better user experience

**Example with Verbose Output:**
```bash
curl -X POST https://your-url.modal.run/query_stream \
  -H "Content-Type: application/json" \
  -d '{"question": "Write a Python function"}' \
  --no-buffer \
  -v
```

---

### 4. GET /service_info - Service Information

**Purpose:** Get information about the background sandbox service

**Endpoint:** `GET /service_info`

**Request:**
```bash
curl https://acme-corp--test-sandbox-http-app.modal.run/service_info
```

**Response:**
```json
{
  "url": "https://...encrypted-tunnel-url...",
  "sandbox_id": "sb-abc123xyz"
}
```

**Response Fields:**
- `url` (string): Encrypted tunnel URL for the background service
- `sandbox_id` (string): Unique identifier for the sandbox instance

**Status Codes:**
- `200 OK`: Information retrieved successfully
- `503 Service Unavailable`: Sandbox not available

**Use Cases:**
- Debugging and troubleshooting
- Monitoring sandbox status
- Internal tooling and administration
- Understanding which sandbox instance is handling requests

**Note:** The `url` field contains an encrypted tunnel URL that's only accessible from within Modal's infrastructure. External clients cannot directly access this URL.

---

## Real-World Usage Examples

### JavaScript/Fetch (Non-Streaming)

```javascript
async function askAgent(question, baseUrl) {
  try {
    const response = await fetch(`${baseUrl}/query`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ question })
    });
    
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    
    const data = await response.json();
    return data.messages.join('\n');
  } catch (error) {
    console.error('Error asking agent:', error);
    throw error;
  }
}

// Usage
const baseUrl = 'https://acme-corp--test-sandbox-http-app.modal.run';
const answer = await askAgent("What is Python?");
console.log(answer);
```

### JavaScript/Fetch (Streaming)

```javascript
async function streamAgentResponse(question, baseUrl, onChunk, onComplete, onError) {
  try {
    const response = await fetch(`${baseUrl}/query_stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ question })
    });
    
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // Keep incomplete line in buffer
      
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data !== '{}') {
            onChunk(data);
          }
        } else if (line.startsWith('event: done')) {
          onComplete();
          return;
        }
      }
    }
  } catch (error) {
    console.error('Error streaming agent response:', error);
    if (onError) onError(error);
  }
}

// Usage
const baseUrl = 'https://acme-corp--test-sandbox-http-app.modal.run';
streamAgentResponse(
  "Explain machine learning",
  baseUrl,
  (chunk) => {
    // Called for each chunk
    process.stdout.write(chunk);
  },
  () => {
    // Called when complete
    console.log('\n\nDone!');
  },
  (error) => {
    // Called on error
    console.error('Stream error:', error);
  }
);
```

### JavaScript/EventSource (Alternative Streaming)

```javascript
// Note: EventSource only supports GET requests, so this requires
// a proxy or modification to support POST. The fetch API approach above
// is recommended for POST requests with streaming.
```

### Python/Requests (Non-Streaming)

```python
import requests
from typing import Dict, List

def ask_agent(question: str, base_url: str) -> Dict:
    """Send a question to the agent and get response.
    
    Args:
        question: The question to ask the agent
        base_url: Base URL of the deployed service
        
    Returns:
        Dictionary with 'ok' and 'messages' keys
        
    Raises:
        requests.HTTPError: If the request fails
    """
    response = requests.post(
        f"{base_url}/query",
        json={"question": question},
        timeout=120,
        headers={"Content-Type": "application/json"}
    )
    response.raise_for_status()
    return response.json()

# Usage
base_url = "https://acme-corp--test-sandbox-http-app.modal.run"
result = ask_agent("What is the weather like?", base_url)

if result["ok"]:
    for message in result["messages"]:
        print(message)
```

### Python/Requests (Streaming)

```python
import requests
import json

def stream_agent_response(question: str, base_url: str):
    """Stream agent responses as they're generated.
    
    Args:
        question: The question to ask the agent
        base_url: Base URL of the deployed service
        
    Yields:
        String chunks of the agent's response
    """
    response = requests.post(
        f"{base_url}/query_stream",
        json={"question": question},
        stream=True,
        timeout=None,
        headers={"Content-Type": "application/json"}
    )
    response.raise_for_status()
    
    for line in response.iter_lines():
        if line:
            line_str = line.decode('utf-8')
            if line_str.startswith('data: '):
                data = line_str[6:]
                if data != '{}':
                    yield data
            elif line_str.startswith('event: done'):
                break

# Usage
base_url = "https://acme-corp--test-sandbox-http-app.modal.run"
for chunk in stream_agent_response("Explain AI", base_url):
    print(chunk, end='', flush=True)
print()  # Newline at end
```

### Python/httpx (Async Streaming)

```python
import httpx
import asyncio

async def stream_agent_response_async(question: str, base_url: str):
    """Async version of streaming agent responses."""
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST",
            f"{base_url}/query_stream",
            json={"question": question},
            headers={"Content-Type": "application/json"}
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith('data: '):
                    data = line[6:]
                    if data != '{}':
                        yield data
                elif line.startswith('event: done'):
                    break

# Usage
async def main():
    base_url = "https://acme-corp--test-sandbox-http-app.modal.run"
    async for chunk in stream_agent_response_async("Explain async Python", base_url):
        print(chunk, end='', flush=True)
    print()

asyncio.run(main())
```

### cURL Examples

**Simple Query:**
```bash
curl -X POST https://your-url.modal.run/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Hello, how are you?"}'
```

**Query with Pretty JSON Output:**
```bash
curl -X POST https://your-url.modal.run/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Python?"}' \
  | jq '.'
```

**Streaming Query:**
```bash
curl -X POST https://your-url.modal.run/query_stream \
  -H "Content-Type: application/json" \
  -d '{"question": "Explain quantum computing"}' \
  --no-buffer
```

**Save Response to File:**
```bash
curl -X POST https://your-url.modal.run/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Write a Python tutorial"}' \
  -o response.json
```

### Postman/Insomnia Setup

1. **Create New Request**
   - Method: `POST`
   - URL: `https://acme-corp--test-sandbox-http-app.modal.run/query`

2. **Headers**
   ```
   Content-Type: application/json
   ```

3. **Body** (select "raw" and "JSON")
   ```json
   {
     "question": "What is the capital of France?"
   }
   ```

4. **Send Request**

### React Component Example

```jsx
import React, { useState } from 'react';

function AgentQuery() {
  const [question, setQuestion] = useState('');
  const [response, setResponse] = useState('');
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  
  const baseUrl = 'https://acme-corp--test-sandbox-http-app.modal.run';
  
  const handleQuery = async () => {
    setLoading(true);
    setResponse('');
    
    try {
      const res = await fetch(`${baseUrl}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question })
      });
      
      const data = await res.json();
      setResponse(data.messages.join('\n'));
    } catch (error) {
      setResponse(`Error: ${error.message}`);
    } finally {
      setLoading(false);
    }
  };
  
  const handleStream = async () => {
    setStreaming(true);
    setResponse('');
    
    try {
      const res = await fetch(`${baseUrl}/query_stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question })
      });
      
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            if (data !== '{}') {
              setResponse(prev => prev + data);
            }
          }
        }
      }
    } catch (error) {
      setResponse(`Error: ${error.message}`);
    } finally {
      setStreaming(false);
    }
  };
  
  return (
    <div>
      <textarea
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        placeholder="Ask a question..."
      />
      <button onClick={handleQuery} disabled={loading || streaming}>
        {loading ? 'Loading...' : 'Query'}
      </button>
      <button onClick={handleStream} disabled={loading || streaming}>
        {streaming ? 'Streaming...' : 'Stream'}
      </button>
      <pre>{response}</pre>
    </div>
  );
}

export default AgentQuery;
```

## Authentication

By default, the endpoints are **publicly accessible**. You can enable authentication using one of these methods:

### Option A: Modal Connect Tokens

**How it works:**
1. Your application generates a connect token for each user
2. User includes the token in the `Authorization` header
3. Modal validates the token and injects `X-Verified-User-Data` header
4. Controller verifies the header

**Enable in your code:**

1. **Settings** (`agent_sandbox/config/settings.py` or environment variable):
   ```python
   enforce_connect_token = True
   ```

2. **Controller** (`agent_sandbox/controllers/controller.py`):
   ```python
   ENFORCE_CONNECT_TOKEN = True
   ```

**User Request:**
```bash
curl -X POST https://your-url.modal.run/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <connect-token>" \
  -d '{"question": "..."}'
```

**JavaScript Example:**
```javascript
const response = await fetch(`${baseUrl}/query`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${connectToken}`
  },
  body: JSON.stringify({ question })
});
```

### Option B: API Keys (via Modal Dashboard)

Configure API keys in the Modal dashboard, then users include:

```bash
curl -X POST https://your-url.modal.run/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <api-key>" \
  -d '{"question": "..."}'
```

**Note:** API key validation happens at the Modal infrastructure level, before requests reach your application.

### Option C: Custom Authentication Middleware

You can add custom authentication middleware to `web_app` in `app.py`:

```python
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    # Your token validation logic here
    if not is_valid_token(token):
        raise HTTPException(status_code=401, detail="Invalid token")
    return token

@web_app.post("/query")
async def query_proxy(
    request: Request,
    body: QueryBody,
    token: str = Depends(verify_token)
):
    # Handler code
    ...
```

## Error Handling

### HTTP Status Codes

Users should handle these status codes:

| Code | Meaning | Action |
|------|---------|--------|
| `200 OK` | Success | Process response normally |
| `400 Bad Request` | Invalid request | Check request body format |
| `401 Unauthorized` | Authentication failed | Verify token/credentials |
| `500 Internal Server Error` | Server error | Retry or contact support |
| `503 Service Unavailable` | Service not ready | Retry after delay (first request) |

### Error Response Format

Error responses follow standard HTTP status codes. Some may include error details:

```json
{
  "detail": "Error message here"
}
```

### Retry Logic Example

```python
import requests
import time
from typing import Optional

def ask_agent_with_retry(
    question: str,
    base_url: str,
    max_retries: int = 3,
    retry_delay: float = 5.0
) -> Optional[dict]:
    """Ask agent with automatic retry on transient errors."""
    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{base_url}/query",
                json={"question": question},
                timeout=120
            )
            
            if response.status_code == 503:
                # Service starting up, wait and retry
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                else:
                    raise Exception("Service unavailable after retries")
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [500, 503]:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
            raise
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            raise
    
    return None

# Usage
result = ask_agent_with_retry(
    "What is Python?",
    "https://your-url.modal.run",
    max_retries=3,
    retry_delay=5.0
)
```

### JavaScript Error Handling

```javascript
async function askAgentWithRetry(question, baseUrl, maxRetries = 3) {
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      const response = await fetch(`${baseUrl}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question })
      });
      
      if (response.status === 503 && attempt < maxRetries - 1) {
        // Service starting up, wait and retry
        await new Promise(resolve => setTimeout(resolve, 5000));
        continue;
      }
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      return await response.json();
    } catch (error) {
      if (attempt === maxRetries - 1) throw error;
      await new Promise(resolve => setTimeout(resolve, 5000));
    }
  }
}
```

## Production Considerations

### Rate Limiting

Modal provides DDoS protection, but you may want to implement per-user rate limiting:

```python
from fastapi import Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
web_app.state.limiter = limiter
web_app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@web_app.post("/query")
@limiter.limit("10/minute")  # 10 requests per minute per IP
async def query_proxy(request: Request, body: QueryBody):
    # Handler code
    ...
```

### Timeouts

**Current Settings:**
- `/query`: 120 seconds timeout
- `/query_stream`: No timeout (streams until complete)

**Adjusting Timeouts:**

In `app.py`, modify the `httpx.Timeout` values:

```python
# For /query
async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
    # Increased to 5 minutes

# For /query_stream
async with httpx.AsyncClient(timeout=None) as client:
    # Already no timeout
```

### CORS Configuration

Current CORS settings allow all origins:

```python
web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ⚠️ Too permissive for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**For Production:**

```python
web_app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://your-frontend-domain.com",
        "https://www.your-frontend-domain.com",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)
```

### Monitoring

**Health Check Monitoring:**
```bash
# Set up cron job or monitoring service
*/5 * * * * curl -f https://your-url.modal.run/health || alert-admin
```

**Response Time Monitoring:**
```python
import time

start = time.time()
response = requests.post(url, json={"question": question})
duration = time.time() - start

if duration > 5.0:  # Alert if > 5 seconds
    send_alert(f"Slow response: {duration}s")
```

### Logging

Monitor these aspects:
- Request rates and patterns
- Response times (p50, p95, p99)
- Error rates by status code
- Sandbox lifecycle events (startup, shutdown)
- Authentication failures

### Cost Optimization

**Strategies:**
1. **Idle Timeout**: Adjust `sandbox_idle_timeout` to balance cost vs latency
2. **Request Batching**: Combine multiple questions into single requests when possible
3. **Caching**: Cache common queries/responses
4. **Resource Limits**: Right-size CPU/memory allocation

**Monitor Costs:**
- Check Modal dashboard for usage metrics
- Set up billing alerts
- Review function invocation counts
- Monitor sandbox uptime

### Security Best Practices

1. **Enable Authentication**: Use Connect tokens or API keys
2. **Restrict CORS**: Only allow your frontend domains
3. **Validate Input**: Sanitize user questions (already handled by Pydantic)
4. **Rate Limiting**: Prevent abuse
5. **Monitor Access**: Log authentication attempts
6. **Keep Secrets Secure**: Never expose API keys in client-side code

### Performance Optimization

**First Request Latency:**
- First request: ~2-5 seconds (sandbox startup)
- Subsequent requests: < 1 second (sandbox is warm)

**Optimization Tips:**
1. Keep sandbox warm with periodic health checks
2. Increase `sandbox_idle_timeout` for high-traffic scenarios
3. Use streaming for better perceived performance
4. Implement client-side caching for common queries

## Related Documentation

- [Architecture Overview](./architecture.md) - Understanding the system architecture
- [Controllers](./controllers.md) - How the background service works
- [Modal Ingress](./modal-ingress.md) - How requests reach your application
- [Configuration](./configuration.md) - Configuration options


# Troubleshooting Guide

This guide covers common issues you may encounter when working with Rafiki and how to resolve them.

Canonical E2E flow reference: `docs/references/runbooks/cloudflare-modal-e2e.md`

## Quick Diagnostics

Before diving into specific issues, run these checks:

```bash
# Derive baseline URLs from checked-in Worker config
export MODAL_API_BASE_URL="$(rg -o '\"MODAL_API_BASE_URL\": \"[^\"]+\"' edge-control-plane/wrangler.jsonc | sed -E 's/.*: \"([^\"]+)\"/\1/')"
export DEV_URL="$MODAL_API_BASE_URL"

# 1. Verify Modal is configured
modal setup

# 2. Check your secrets exist
modal secret list

# 3. Test a simple run
modal run -m modal_backend.main

# 4. Check Cloudflare control plane health
curl "https://<your-worker>.workers.dev/health"

# 5. Check background sandbox info (internal only)
curl "${DEV_URL}/service_info" -H "X-Internal-Auth: <internal-token>"
```

---

## Startup & Configuration Issues

### "Secret not found" Error

```
modal.exception.NotFoundError: Secret 'openai-secret' not found
```

**Cause**: The required Modal secret hasn't been created.

**Solution**:
```bash
modal secret create openai-secret OPENAI_API_KEY=your-key-here
```

**Verify**:
```bash
modal secret list
# Should show: openai-secret
```

---

### "Invalid API key" or Authentication Errors

```
openai.AuthenticationError: Invalid API key
```

**Cause**: The API key is incorrect or expired.

**Solutions**:

1. Verify your API key is valid at [platform.openai.com](https://platform.openai.com)

2. Recreate the secret with the correct key:
   ```bash
   modal secret delete openai-secret
   modal secret create openai-secret OPENAI_API_KEY=sk-...
   ```

3. Ensure there are no extra spaces or quotes in the key value

---

### "Modal not configured" Error

```
modal.exception.AuthError: Not authenticated
```

**Cause**: Modal CLI hasn't been set up.

**Solution**:
```bash
modal setup
# Follow the prompts to authenticate
```

---

### "Missing internal auth token" (401)

**Symptoms:**
- `{"ok": false, "error": "Missing internal auth token"}`
- HTTP 401 from Modal gateway endpoints (`/query`, `/query_stream`, `/submit`, `/jobs/*`)

**Cause:** Modal endpoints now require `X-Internal-Auth` on all non-health requests.

**Solutions:**

1. **Use the Cloudflare Worker for public traffic** (recommended).
2. **For internal calls**, include the header:
   ```bash
   -H "X-Internal-Auth: <internal-token>"
   ```
3. **Ensure the secret exists**:
   ```bash
  modal secret create internal-auth-secret INTERNAL_AUTH_SECRET=<same-as-cloudflare>
```

---

### "Unauthorized" from Cloudflare Worker (401)

**Symptoms:**
- `{"ok": false, "error": "Unauthorized"}`
- HTTP 401 on Worker endpoints (except `/health`)

**Cause:** Missing or invalid session token (`Authorization: Bearer <token>` or `token=<token>` for WebSockets).

**Solutions:**

1. **Include the Authorization header** on all public requests.
2. **Regenerate token using the helper script**:
   ```bash
   TOKEN="$(node edge-control-plane/scripts/generate-session-token.js \
     --user-id e2e-user \
     --tenant-id e2e-tenant \
     --session-id sess-e2e-001 \
     --ttl-seconds 3600 \
     --secret "$SESSION_SIGNING_SECRET")"
   ```
3. **Verify `session_id` in requests is authorized by token claims**.

---

### "Too Many Requests" from Cloudflare Worker (429)

**Symptoms:**
- `{"ok": false, "error": "Rate limit exceeded"}`
- HTTP 429 responses under load

**Cause:** Edge rate limits exceeded for the user/session.

**Solutions:**

1. Reduce request frequency or batch calls.
2. Confirm rate limit thresholds in Cloudflare configuration.
3. Validate the Rate Limiting binding (`RATE_LIMITER`) is configured in `wrangler.jsonc`.

---

### "Session not found" when using `session_key`

**Symptoms:**
- New session created instead of resuming
- `session_id` changes unexpectedly across requests

**Cause:** KV mapping for `session_key` is missing or expired.

**Solutions:**

1. Persist and reuse the returned `session_id` when possible.
2. Ensure `SESSION_CACHE` is configured and reachable.
3. Re-send `session_key` to rebuild the mapping (default TTL 30 days).

## Sandbox Issues

### "Failed to start background sandbox or get service URL"

**Cause**: The 30-second timeout for tunnel discovery was exceeded, or the sandbox failed to start.

**Possible reasons**:
- Network connectivity issues
- Modal service disruption
- Resource limits exceeded

**Solutions**:

1. **Check Modal status**: Visit [status.modal.com](https://status.modal.com)

2. **Try a simple run first**:
   ```bash
   modal run -m modal_backend.main
   ```
   If this works, the issue is specific to the long-lived service pattern.

3. **Check for existing sandboxes**:
   ```bash
   # List running sandboxes
   modal container list
   ```

4. **Terminate and restart**:
   ```bash
   modal run -m modal_backend.main::terminate_service_sandbox
   modal serve -m modal_backend.main
   ```

5. **Check logs**:
   ```bash
   make tail-logs
   # Or
   modal run -m modal_backend.main::tail_logs
   ```

---

### Sandbox Runs Out of Memory (OOMKilled)

**Symptoms**:
- Sandbox crashes unexpectedly
- "OOMKilled" in Modal logs
- Agent responses cut off mid-stream

**Cause**: The sandbox doesn't have enough memory for your workload.

**Solution**: Increase memory allocation:

```bash
# Via environment variable
export SANDBOX_MEMORY=4096
modal serve -m modal_backend.main

# Or edit modal_backend/settings/settings.py
sandbox_memory: int = 4096  # Increase from 2048
```

**Memory guidelines**:
| Workload | Recommended Memory |
|----------|-------------------|
| Simple Q&A | 1024 MB |
| Standard agent tasks | 2048 MB |
| Large context / file processing | 4096 MB |
| Heavy computation | 8192 MB |

---

### Slow Response Times / Cold Starts

**Symptoms**: First request after a period of inactivity takes 10-30 seconds.

**Cause**: The sandbox was terminated due to idle timeout and needs to restart.

**Solutions**:

1. **Increase idle timeout** (keeps sandbox warm longer):
   ```bash
   export SANDBOX_IDLE_TIMEOUT=1800  # 30 minutes
   modal serve -m modal_backend.main
   ```

2. **Use a gateway ping** to keep the sandbox warm:
   ```bash
   # Run every 5 minutes via cron or external service
   # /service_info triggers sandbox discovery (internal-only)
   curl "${DEV_URL}/service_info" -H "X-Internal-Auth: <internal-token>"
   ```

3. **Accept cold starts** if traffic is sporadic (saves costs)

---

### Hot-Reload Conflict (Modal serve + Wrangler)

**Symptoms:**
- `modal serve` crashes or restarts unexpectedly
- Errors during dev when Wrangler updates its local SQLite state

**Cause:** Wrangler’s dev server can modify its local SQLite state while Modal hot-reload is running, which can cause file contention.

**Workaround:**
1. Stop both dev servers.
2. Restart `modal serve -m modal_backend.main`.
3. Restart `wrangler dev` for the Cloudflare worker.

If the issue persists, terminate and recreate the background sandbox:
```bash
modal run -m modal_backend.main::terminate_service_sandbox
modal serve -m modal_backend.main
```

---

## File Persistence Issues

### Files Disappear After Restart

**Cause**: Files were written to the wrong location (not `/data`).

**Solution**: Always write to `/data/`:

```python
# ✅ Correct - persists across restarts
with open("/data/output.txt", "w") as f:
    f.write("This will persist")

# ❌ Wrong - lost on restart
with open("/tmp/output.txt", "w") as f:
    f.write("This will be lost")

with open("/root/output.txt", "w") as f:
    f.write("This will also be lost")
```

**Verify persistence**:
```bash
# Write a file
curl -X POST "${DEV_URL}/query" \
  -H 'Content-Type: application/json' \
  -d '{"question":"Write hello to /data/test.txt"}'

# Restart the sandbox
modal run -m modal_backend.main::terminate_service_sandbox
modal serve -m modal_backend.main

# Check if file exists
curl -X POST "${DEV_URL}/query" \
  -H 'Content-Type: application/json' \
  -d '{"question":"Read /data/test.txt"}'
```

---

### Volume Not Mounting

**Symptoms**: `/data` directory doesn't exist or isn't writable.

**Cause**: Volume configuration issue.

**Solutions**:

1. **Check volume exists**:
   ```bash
   modal volume list
   ```

2. **Verify volume name matches settings**:
   ```python
   # In modal_backend/settings/settings.py
   persist_vol_name: str = "svc-runner-8001-vol"
   ```

3. **Create volume manually if needed**:
   ```bash
   modal volume create svc-runner-8001-vol
   ```

---

## Tool Execution Issues

### Tool Execution Denied / Permission Error

**Cause**: The tool isn't in the allowed tools list or permission mode is blocking it.

**Solution**: Add the tool to the allowed list in `modal_backend/mcp_tools/registry.py`:

```python
# Find the _allowed_tools list
_allowed_tools = [
    "Read",
    "Write",
    "WebSearch(*)",
    "WebFetch(*)",
    "mcp__utilities__calculate",
    # Add your tool here:
    "mcp__utilities__your_new_tool",
]
```

**Tool naming convention**:
- Built-in tools: `"ToolName"` or `"ToolName(*)"`
- Custom MCP tools: `"mcp__<server>__<tool>"`

---

### Tool Returns Empty or Unexpected Results

**Possible causes**:

1. **Tool implementation error**: Check the tool code in `modal_backend/mcp_tools/`

2. **Incorrect arguments**: Verify the tool schema matches what the agent is sending

3. **Tool timeout**: Long-running tools may exceed internal timeouts

**Debug steps**:

1. Check the tool directly:
   ```python
   from modal_backend.mcp_tools.calculate_tool import calculate
   result = await calculate({"expression": "2 + 2"})
   print(result)
   ```

2. Enable verbose logging:
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   ```

---

## HTTP Endpoint Issues

### 404 Not Found

**Cause**: Hitting the wrong endpoint or service isn't running.

**Solutions**:

1. **Verify the service is running**:
   ```bash
   modal serve -m modal_backend.main
   # Note the URL in the output
   ```

2. **Check the correct endpoint**:
   ```bash
   # Available endpoints:
   GET  /health         # Gateway health
  GET  /health_check   # Service health (internal-only, controller)
   POST /query          # Agent query
   POST /query_stream   # Streaming query
   GET  /service_info   # Sandbox information
   ```

3. **Use the correct URL** from `modal serve` output

---

### 502 Bad Gateway

**Cause**: The HTTP gateway can't reach the background sandbox.

**Solutions**:

1. **Check if sandbox is running**:
   ```bash
   curl "${DEV_URL}/service_info"
   ```

2. **Restart the service**:
   ```bash
   modal run -m modal_backend.main::terminate_service_sandbox
   modal serve -m modal_backend.main
   ```

3. **Check for errors in logs**:
   ```bash
   make tail-logs
   ```

---

### CORS Errors (Browser)

**Symptoms**: Browser console shows CORS policy errors.

**Cause**: The frontend domain isn't allowed by CORS configuration.

**Solution**: Update CORS settings in `modal_backend/main.py`:

```python
web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-frontend.com"],  # Add your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

For development, you can temporarily allow all origins:
```python
allow_origins=["*"]  # Not recommended for production
```

---

## Streaming Issues

### Streaming Responses Cut Off

**Cause**: Client or intermediate proxy timeout.

**Solutions**:

1. **Increase client timeout**:
   ```python
   # Python httpx
   async with httpx.AsyncClient(timeout=300) as client:
       async with client.stream("POST", url, json=body) as response:
           ...
   ```

2. **Use EventSource for browser** (handles reconnection):
   ```javascript
   const eventSource = new EventSource(url);
   eventSource.onmessage = (event) => {
       console.log(event.data);
   };
   ```

---

### SSE Events Not Parsing Correctly

**Cause**: Incorrect SSE format handling.

**Expected format**:
```
data: {"type": "message", "content": "..."}

data: {"type": "message", "content": "..."}

event: done
data: {}
```

**Solution**: Parse each `data:` line as JSON:
```python
for line in response.iter_lines():
    if line.startswith("data: "):
        data = json.loads(line[6:])
        print(data)
```

---

## Getting Help

If you're still stuck:

1. **Check Modal logs**:
   ```bash
   make tail-logs
   ```

2. **Check Modal dashboard**: [modal.com/apps](https://modal.com/apps)

3. **Review configuration**:
   ```python
   from modal_backend.settings.settings import Settings
   settings = Settings()
   print(settings.model_dump())
   ```

4. **File an issue**: Include:
   - Error message
   - Steps to reproduce
   - `modal --version` output
   - Relevant configuration

---

## Related Documentation

- [Configuration Guide](./configuration.md) - All settings and their effects
- [Architecture Overview](../design-docs/architecture-overview.md) - Understanding how components interact
- [Controllers](../design-docs/controllers-background-service.md) - Deep dive into the background service

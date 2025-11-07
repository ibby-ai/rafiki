# Modal Ingress: How HTTP Requests Reach Your Application

This document explains how Modal handles HTTP ingress (incoming traffic) and routes requests to your application.

## What is Ingress?

**Ingress** refers to the infrastructure that accepts incoming HTTP/HTTPS requests from external clients and routes them to your application. In traditional deployments, you might use:

- Load balancers (AWS ALB, GCP Load Balancer)
- API gateways (Kong, AWS API Gateway)
- Reverse proxies (nginx, Traefik)
- Ingress controllers (Kubernetes Ingress)

**With Modal, ingress is fully managed** - you don't need to configure any of these components yourself.

## Modal's Ingress Architecture

### Overview

Modal provides a **fully managed ingress layer** that:

1. **Accepts HTTPS requests** on public URLs
2. **Terminates TLS/SSL** (handles SSL certificates automatically)
3. **Routes to your functions** based on the `@modal.asgi_app()` decorator
4. **Handles authentication** (Modal Connect tokens, API keys)
5. **Provides DDoS protection** and rate limiting
6. **Manages load balancing** and auto-scaling

### Public URLs

When you deploy with `modal serve` or `modal deploy`, Modal generates public URLs:

**Development:**
```
https://<org>--test-sandbox-http-app-dev.modal.run
```

**Production:**
```
https://<org>--test-sandbox-http-app.modal.run
```

**Components:**
- `<org>`: Your Modal organization name
- `test-sandbox`: Your Modal app name (from `modal.App("test-sandbox")`)
- `http-app`: Function name (from `@modal.asgi_app()` decorator)
- `-dev`: Suffix for development deployments (omitted in production)

### How It Works

```
┌─────────────────────────────────────────────────────────────┐
│                    Internet / External Clients               │
│  curl, browser, API clients, etc.                           │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            │ HTTPS Request
                            │ POST /query
                            │ Host: <org>--test-sandbox-http-app-dev.modal.run
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              Modal Infrastructure (Managed)                   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  TLS Termination                                     │   │
│  │  - Validates SSL certificate                        │   │
│  │  - Decrypts HTTPS → HTTP                            │   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Routing Layer                                       │   │
│  │  - Parses Host header                               │   │
│  │  - Identifies target app/function                    │   │
│  │  - Routes to appropriate Modal function              │   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Authentication & Authorization                      │   │
│  │  - Validates Modal Connect tokens                    │   │
│  │  - Checks API keys (if configured)                   │   │
│  │  - Injects verified headers                          │   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Load Balancing & Auto-scaling                       │   │
│  │  - Distributes requests across function instances    │   │
│  │  - Scales up/down based on traffic                   │   │
│  └──────────────────────────────────────────────────────┘   │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            │ HTTP Request (decrypted)
                            │ POST /query
                            │ X-Verified-User-Data: {...}
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              Your Application (http_app)                     │
│  @modal.asgi_app() decorated function                       │
│  Returns FastAPI app instance                               │
└─────────────────────────────────────────────────────────────┘
```

## The @modal.asgi_app() Decorator

### What It Does

The `@modal.asgi_app()` decorator tells Modal:

1. **This function should be exposed as an HTTP endpoint**
2. **The function returns an ASGI application** (FastAPI, Starlette, etc.)
3. **Modal should route HTTP requests to this function**

### Example

```python
@app.function(image=agent_sdk_image, secrets=agent_sdk_secrets)
@modal.asgi_app()
def http_app():
    """ASGI app exposing HTTP endpoints for the agent service."""
    return web_app
```

**Key points:**
- `@app.function()` defines a Modal function with image and secrets
- `@modal.asgi_app()` marks it as an HTTP endpoint
- Function returns an ASGI app (FastAPI instance)
- Modal automatically creates public URL and routes traffic

### ASGI Protocol

**ASGI (Asynchronous Server Gateway Interface)** is the Python standard for async web applications. Modal uses ASGI to:

- Accept HTTP requests from Modal infrastructure
- Pass requests to your ASGI app (FastAPI, Starlette, etc.)
- Stream responses back to clients
- Handle WebSocket connections (if supported)

**Common ASGI frameworks:**
- FastAPI (used in this project)
- Starlette
- Django (with ASGI support)
- Quart

## Request Flow: Step by Step

### 1. Client Makes Request

```bash
curl -X POST 'https://<org>--test-sandbox-http-app-dev.modal.run/query' \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the capital of Canada?"}'
```

### 2. Modal Infrastructure Receives Request

- **DNS resolution:** Client resolves hostname to Modal's IP addresses
- **TLS handshake:** Modal presents SSL certificate, establishes encrypted connection
- **Request parsing:** Modal parses HTTP method, path, headers, body

### 3. Routing Decision

Modal examines:
- **Host header:** `<org>--test-sandbox-http-app-dev.modal.run`
- **App name:** Extracted as `test-sandbox`
- **Function name:** Extracted as `http-app` (from decorator)
- **Path:** `/query`

Modal routes to: `test-sandbox` app → `http_app` function → `/query` endpoint

### 4. Authentication (Optional)

If Modal Connect tokens are enabled:

```python
# In http_app handler
if settings.enforce_connect_token:
    creds = await sb.create_connect_token.aio(user_metadata={"ip": request.client.host})
    headers = {"Authorization": f"Bearer {creds.token}"}
```

Modal validates the token and injects `X-Verified-User-Data` header.

### 5. Function Invocation

Modal:
- Spins up function instance (or reuses existing)
- Passes HTTP request to ASGI app
- Your `http_app` function is called
- Returns `web_app` (FastAPI instance)
- Modal invokes FastAPI's ASGI interface

### 6. FastAPI Handles Request

```python
@web_app.post("/query")
async def query_proxy(request: Request, body: QueryBody):
    # Your handler code executes here
    sb, url = await get_or_start_background_sandbox_aio()
    # ... proxy to background service
```

### 7. Response Path

- FastAPI generates HTTP response
- Modal infrastructure receives response
- TLS encryption applied
- Response sent back to client

## Key Features of Modal Ingress

### Automatic SSL/TLS

**You don't need to:**
- Generate SSL certificates
- Configure certificate renewal
- Set up Let's Encrypt
- Manage certificate storage

**Modal handles:**
- Automatic certificate provisioning
- Certificate renewal
- TLS 1.2+ support
- Perfect Forward Secrecy

### DDoS Protection

Modal's infrastructure includes:
- Rate limiting
- DDoS mitigation
- Request filtering
- Geographic routing

**No configuration required** - it's built-in.

### Auto-scaling

Modal automatically:
- Scales up function instances during high traffic
- Scales down during low traffic
- Maintains minimum instances for low latency
- Handles traffic spikes gracefully

### Load Balancing

Modal distributes requests:
- Across multiple function instances
- Based on instance health
- With automatic failover
- Using intelligent routing

## Security Considerations

### Modal Connect Tokens

For per-request authentication:

```python
# Generate token in http_app
creds = await sb.create_connect_token.aio(user_metadata={"ip": request.client.host})
headers = {"Authorization": f"Bearer {creds.token}"}

# Validate in controller
if not request.headers.get("X-Verified-User-Data"):
    raise HTTPException(status_code=401)
```

**Benefits:**
- Per-request authentication
- User metadata tracking
- Token expiration
- Revocable tokens

### API Keys (Alternative)

Modal also supports API key authentication:
- Configure in Modal dashboard
- Pass via `Authorization: Bearer <key>` header
- Validated by Modal infrastructure

### Network Isolation

- Functions run in isolated containers
- No direct network access between functions
- Sandbox isolation for background services
- Encrypted ports for internal communication

## Monitoring and Observability

### Request Logs

Modal automatically logs:
- Request method and path
- Response status codes
- Request/response sizes
- Latency metrics

**Access via:**
- Modal dashboard
- `modal app logs <app-name>`
- Function-specific logs

### Metrics

Modal provides:
- Request rate (requests/second)
- Error rate
- Latency percentiles (p50, p95, p99)
- Function invocation counts

### Tracing

For debugging:
- Request IDs in logs
- Function execution traces
- Error stack traces
- Performance profiling

## Comparison: Modal vs Traditional Ingress

| Feature | Traditional | Modal |
|---------|------------|-------|
| SSL/TLS | Manual setup | Automatic |
| Load Balancing | Configure ALB/nginx | Built-in |
| Auto-scaling | Configure autoscaling | Automatic |
| DDoS Protection | Cloud provider WAF | Built-in |
| Certificate Management | Let's Encrypt + renewal | Automatic |
| Configuration | Complex YAML/JSON | Decorator only |
| Cost | Pay for infrastructure | Pay per request |

## Best Practices

### 1. Use Descriptive Function Names

```python
@modal.asgi_app()
def http_app():  # ✅ Clear name
    return web_app

@modal.asgi_app()
def api():  # ❌ Too generic
    return app
```

### 2. Handle Errors Gracefully

```python
@web_app.post("/query")
async def query_proxy(request: Request, body: QueryBody):
    try:
        # Your logic
    except Exception as e:
        logger.exception("Error processing query")
        raise HTTPException(status_code=500, detail=str(e))
```

### 3. Set Appropriate Timeouts

```python
async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
    # Long-running requests need longer timeouts
```

### 4. Use Health Checks

```python
@web_app.get("/health")
async def health():
    return {"ok": True}
```

### 5. Monitor Performance

- Review Modal dashboard for metrics
- Set up alerts for error rates
- Track latency percentiles
- Monitor function cold-start times

## Troubleshooting

### 502 Bad Gateway

**Cause:** Function crashed or timed out

**Solutions:**
- Check function logs
- Review error messages
- Increase function timeout
- Check resource limits (memory, CPU)

### 503 Service Unavailable

**Cause:** No function instances available

**Solutions:**
- Check function is deployed
- Verify `@modal.asgi_app()` decorator
- Check Modal dashboard for errors
- Review function startup logs

### High Latency

**Cause:** Cold-start or slow function execution

**Solutions:**
- Use background sandbox pattern (this project)
- Optimize function startup time
- Pre-warm functions (keep minimum instances)
- Review function code for bottlenecks

### SSL Certificate Errors

**Cause:** Rare - usually client-side issue

**Solutions:**
- Verify URL is correct
- Check Modal dashboard for certificate status
- Try different client (curl, browser, etc.)
- Contact Modal support if persistent

## Related Documentation

- [Architecture Overview](./architecture.md) - How ingress fits into overall architecture
- [Controllers](./controllers.md) - How requests reach the background service
- [Modal Documentation](https://modal.com/docs/guide/container-lifecycle) - Official Modal docs


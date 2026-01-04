"""
Entry-point and Modal function definitions for running the agent in a sandboxed
environment and exposing lightweight HTTP endpoints.

Quickstart (CLI):
- run local_entrypoint: `modal run -m agent_sandbox.app` (runs the agent once in a short-lived Modal function)
- run run_agent_remote: `modal run -m agent_sandbox.app::run_agent_remote --question "..."`
- keep dev deployment running: `modal serve -m agent_sandbox.app`
- deploy to production: `modal deploy -m agent_sandbox.deploy`

Notes for future maintainers:
- This file defines a `modal.App` plus several `@app.function` entries. Functions
  annotated with `@modal.asgi_app` are exposed as HTTP endpoints when the
  app is served or deployed. See Modal docs for `modal.App`, `@app.function`,
  `modal.Sandbox`, and `@modal.asgi_app` for lifecycle and limits.
- We use a long-running `modal.Sandbox` process to host a FastAPI microservice
  (`agent_sandbox.controllers.controller`) and then call into it from a short-lived Modal
  function. This pattern keeps cold-start latency low for the model runtime
  while allowing us to keep the HTTP frontdoor responsive.

Prerequisite for curl testing:
- Start the dev server locally with `modal serve -m agent_sandbox.app` so the HTTP endpoint
  (see `http_app`) is reachable at a dev URL like
  `https://<org>--test-sandbox-http-app-dev.modal.run`.
"""

import inspect
import logging
import time
import urllib.error
import urllib.request

import anyio
import httpx
import modal
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from modal import exception as modal_exc
from starlette.responses import StreamingResponse

from agent_sandbox.config.settings import Settings, get_modal_secrets
from agent_sandbox.images import get_agent_image
from agent_sandbox.jobs import (
    JOB_QUEUE,
    bump_attempts,
    cancel_job,
    enqueue_job,
    get_job_status,
    should_skip_job,
    update_job,
)
from agent_sandbox.prompts.prompts import DEFAULT_QUESTION, SYSTEM_PROMPT
from agent_sandbox.schemas import JobStatusResponse, JobSubmitRequest, JobSubmitResponse, QueryBody

app = modal.App("test-sandbox")
_settings = Settings()

web_app = FastAPI()

web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _autoscale_kwargs() -> dict[str, int]:
    """Build autoscaling kwargs for Modal functions when configured.

    Modal autoscaling parameters:
        - min_containers: Minimum always-warm containers (reduces cold starts)
        - max_containers: Maximum concurrent containers (cost/capacity limit)
        - buffer_containers: Extra warm containers beyond current demand
        - scaledown_window: Seconds to wait before scaling down idle containers

    See: https://modal.com/docs/guide/cold-start#scaling-settings
    """
    kwargs: dict[str, int] = {}
    if _settings.min_containers is not None:
        kwargs["min_containers"] = _settings.min_containers
    if _settings.max_containers is not None:
        kwargs["max_containers"] = _settings.max_containers
    if _settings.buffer_containers is not None:
        kwargs["buffer_containers"] = _settings.buffer_containers
    if _settings.scaledown_window is not None:
        kwargs["scaledown_window"] = _settings.scaledown_window
    return kwargs


def _function_resource_kwargs() -> dict[str, object]:
    """Build resource kwargs for Modal functions."""
    kwargs: dict[str, object] = {}
    if _settings.sandbox_cpu_limit is not None:
        kwargs["cpu"] = (_settings.sandbox_cpu, _settings.sandbox_cpu_limit)
    else:
        kwargs["cpu"] = _settings.sandbox_cpu

    if _settings.sandbox_memory_limit is not None:
        kwargs["memory"] = (_settings.sandbox_memory, _settings.sandbox_memory_limit)
    else:
        kwargs["memory"] = _settings.sandbox_memory

    ephemeral_disk = _validated_ephemeral_disk()
    if ephemeral_disk is not None:
        kwargs["ephemeral_disk"] = ephemeral_disk
    return kwargs


def _sandbox_resource_kwargs() -> dict[str, object]:
    """Build resource kwargs for Modal sandboxes."""
    kwargs: dict[str, object] = {}
    if _settings.sandbox_cpu_limit is not None:
        kwargs["cpu"] = (_settings.sandbox_cpu, _settings.sandbox_cpu_limit)
    else:
        kwargs["cpu"] = _settings.sandbox_cpu

    if _settings.sandbox_memory_limit is not None:
        kwargs["memory"] = (_settings.sandbox_memory, _settings.sandbox_memory_limit)
    else:
        kwargs["memory"] = _settings.sandbox_memory
    ephemeral_disk = _validated_ephemeral_disk()
    if ephemeral_disk is not None and _sandbox_supports_ephemeral_disk():
        kwargs["ephemeral_disk"] = ephemeral_disk
    return kwargs


def _sandbox_supports_ephemeral_disk() -> bool:
    """Return True if modal.Sandbox.create accepts ephemeral_disk."""
    try:
        return "ephemeral_disk" in inspect.signature(modal.Sandbox.create).parameters
    except (TypeError, ValueError):
        return False


def _validated_ephemeral_disk() -> int | None:
    """Validate ephemeral disk size against Modal limits."""
    if _settings.sandbox_ephemeral_disk is None:
        return None
    max_mib = 3145728
    if _settings.sandbox_ephemeral_disk <= 0:
        logging.getLogger(__name__).warning(
            "sandbox_ephemeral_disk=%s MiB must be positive; skipping",
            _settings.sandbox_ephemeral_disk,
        )
        return None
    if _settings.sandbox_ephemeral_disk > max_mib:
        logging.getLogger(__name__).warning(
            "sandbox_ephemeral_disk=%s MiB exceeds Modal maximum %s; skipping",
            _settings.sandbox_ephemeral_disk,
            max_mib,
        )
        return None
    return _settings.sandbox_ephemeral_disk


def _function_runtime_kwargs(
    *, include_retries: bool = True, include_autoscale: bool = True
) -> dict[str, object]:
    """Combine autoscaling and resource tuning for Modal functions."""
    kwargs: dict[str, object] = {}
    kwargs.update(_function_resource_kwargs())
    if include_autoscale:
        kwargs.update(_autoscale_kwargs())
    if include_retries:
        kwargs.update(_retry_kwargs())
    return kwargs


def _maybe_concurrent():
    """Return a concurrency decorator when configured, otherwise no-op."""
    if _settings.concurrent_max_inputs is None and _settings.concurrent_target_inputs is None:
        return lambda fn: fn
    return modal.concurrent(
        max_inputs=_settings.concurrent_max_inputs,
        target_inputs=_settings.concurrent_target_inputs,
    )


def _retry_policy() -> modal.Retries | None:
    """Build a Modal retry policy for transient failure recovery.

    Uses exponential backoff: delay = initial_delay * (backoff_coefficient ^ attempt)
    Delays are capped at max_delay to prevent unbounded waits.

    Defaults (when settings provided): 2x backoff, 1s initial, 60s max.
    Returns None if retry_max_attempts is not configured.

    See: https://modal.com/docs/guide/retries
    """
    if _settings.retry_max_attempts is None:
        return None
    return modal.Retries(
        max_retries=_settings.retry_max_attempts,
        # Exponential backoff: delay doubles each retry (2.0 coefficient)
        backoff_coefficient=_settings.retry_backoff_coefficient or 2.0,
        initial_delay=_settings.retry_initial_delay or 1.0,  # First retry after 1s
        max_delay=_settings.retry_max_delay or 60.0,  # Cap at 60s between retries
    )


def _retry_kwargs() -> dict[str, object]:
    policy = _retry_policy()
    if not policy:
        return {}
    return {"retries": policy}


def _job_queue_schedule() -> modal.Cron | None:
    cron = _settings.job_queue_cron
    if not cron:
        return None
    return modal.Cron(cron)


# Create image and secrets
agent_image = get_agent_image(_settings)
agent_secrets = get_modal_secrets()


@app.function(
    image=agent_image,
    secrets=agent_secrets,
    **_function_runtime_kwargs(include_retries=False),
)
@_maybe_concurrent()
# requires_proxy_auth: When True, requests must include Modal workspace auth token.
# Protects public endpoints from unauthorized access. Set via require_proxy_auth setting.
# See: https://modal.com/docs/guide/webhooks#proxy-authentication
# custom_domains: Production-ready branding with custom domain names.
# See: https://modal.com/docs/guide/webhooks#custom-domains
@modal.asgi_app(
    requires_proxy_auth=_settings.require_proxy_auth,
    custom_domains=_settings.custom_domains or [],
)
def http_app():
    """ASGI app exposing HTTP endpoints for the agent service."""
    return web_app


@web_app.get("/health")
async def health():
    """Health check endpoint."""
    return {"ok": True}


@web_app.post("/query")
async def query_proxy(request: Request, body: QueryBody):
    """Proxy query requests to the background sandbox service."""
    # Use async getter to avoid blocking event loop
    sb, url = await get_or_start_background_sandbox_aio()

    # Optional: per-request connect token (verified in sandbox service)
    headers = {}
    settings = Settings()
    if settings.enforce_connect_token:
        creds = await sb.create_connect_token.aio(
            user_metadata={"ip": request.client.host or "unknown"}
        )
        headers = {"Authorization": f"Bearer {creds.token}"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        r = await client.post(
            f"{url.rstrip('/')}/query",
            json=body.model_dump(),
            headers=headers,
            timeout=httpx.Timeout(120.0, connect=30.0),
        )
        r.raise_for_status()
        return r.json()


@web_app.post("/query_stream")
async def query_stream(request: Request, body: QueryBody):
    """Stream query responses from the background sandbox service."""
    sb, url = await get_or_start_background_sandbox_aio()

    headers = {}
    settings = Settings()
    if settings.enforce_connect_token:
        creds = await sb.create_connect_token.aio(
            user_metadata={"ip": request.client.host or "unknown"}
        )
        headers = {"Authorization": f"Bearer {creds.token}"}

    async def sse_proxy():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", f"{url.rstrip('/')}/query_stream", json=body.model_dump(), headers=headers
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        sse_proxy(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@web_app.post("/submit", response_model=JobSubmitResponse)
async def submit_job(body: JobSubmitRequest) -> JobSubmitResponse:
    """Enqueue a background job and return its id."""
    job_id = enqueue_job(body.question)
    return JobSubmitResponse(job_id=job_id)


@web_app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def job_status(job_id: str) -> JobStatusResponse:
    """Fetch job status and result (if available)."""
    status = get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    return status


@web_app.delete("/jobs/{job_id}", response_model=JobStatusResponse)
async def cancel_job_request(job_id: str) -> JobStatusResponse:
    """Cancel a queued job."""
    status = cancel_job(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    return status


@web_app.get("/service_info")
async def service_info():
    """Get information about the background sandbox service."""
    sb, url = await get_or_start_background_sandbox_aio()
    return {"url": url, "sandbox_id": sb.object_id}


@app.function(image=agent_image, secrets=agent_secrets)
async def tail_logs(n: int = 200, timeout: float = 2.0) -> list[str]:
    """Tail logs from the background sandbox.

    Args:
        n: Maximum number of log lines to return.
        timeout: Timeout in seconds for log collection.

    Returns:
        List of log lines.
    """
    sb, _ = await get_or_start_background_sandbox_aio()
    from collections import deque

    buf = deque(maxlen=n)
    async with anyio.move_on_after(timeout):
        async for msg in sb.stdout.aio():
            for line in str(msg).splitlines():
                buf.append(line)
    return list(buf)


# Persistent registry for sandbox metadata (survives sandbox restarts).
# Keys are sandbox names (e.g., SANDBOX_NAME), values are dicts with:
#   - id: Sandbox object_id
#   - url: Service tunnel URL
#   - volume: Name of attached persistent volume
#   - created_at: Unix timestamp of creation
#   - tags: Dict of sandbox tags (role, app, port)
#   - status: Current status ("running", "missing")
SESSIONS = modal.Dict.from_name("sandbox-sessions", create_if_missing=True)

# Service sandbox identity and config (will be initialized from Settings)
SANDBOX_NAME = _settings.sandbox_name
SERVICE_PORT = _settings.service_port
PERSIST_VOL_NAME = _settings.persist_vol_name


def _get_persist_volume() -> modal.Volume:
    """Return the configured persistent volume handle."""
    kwargs: dict[str, object] = {"create_if_missing": True}
    if _settings.persist_vol_version is not None:
        kwargs["version"] = _settings.persist_vol_version
    return modal.Volume.from_name(PERSIST_VOL_NAME, **kwargs)


# =============================================================================
# GLOBAL STATE MANAGEMENT
# =============================================================================
# These module-level globals store handles to the background sandbox and its URL.
#
# WHY THIS WORKS IN MODAL:
# - Each Modal worker process has its own isolated Python interpreter
# - Within a single worker, multiple requests share the same module state
# - This means subsequent requests in the same worker reuse the existing sandbox
#   connection instead of creating a new one (avoiding cold-start latency)
#
# IMPORTANT CAVEATS:
# - Different Modal workers will each have their own SANDBOX/SERVICE_URL
# - That's OK because they all discover the SAME sandbox via `from_name()`
# - If the sandbox dies, the next request will detect this and create a new one
# =============================================================================
SANDBOX: modal.Sandbox | None = None
SERVICE_URL: str | None = None


def _wait_for_service(url: str, timeout: int = 60, path: str = "/health_check") -> None:
    """Block until an HTTP health check returns 200 OK.

    Args:
        url: Base URL of the service (including scheme and host).
        timeout: Maximum time to wait in seconds.
        path: Health check path to append to URL.

    Raises:
        TimeoutError: If the service does not become healthy in time.
    """
    check_url = f"{url.rstrip('/')}{path}"
    start = time.time()
    delay = 0.5
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(check_url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            time.sleep(delay)
            delay = min(delay * 1.5, 3.0)
    raise TimeoutError(f"Service {check_url} did not become available within {timeout} seconds")


# Cron job that runs every 2 minutes to verify sandbox health and update SESSIONS metadata.
# If the sandbox has died, marks it as "missing" so get_or_start_background_sandbox()
# will create a new one on the next request.
@app.function(
    image=agent_image,
    secrets=agent_secrets,
    schedule=modal.Cron("*/2 * * * *"),
    **_retry_kwargs(),
)
def cleanup_sessions():
    """Verify sandbox health and update SESSIONS registry.

    Runs every 2 minutes via cron. Checks if the named sandbox is still alive
    by attempting to fetch its tunnel URLs. Updates SESSIONS status accordingly.
    """
    try:
        sb = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)
        _ = sb.tunnels()  # Will raise NotFoundError if sandbox is gone
        SESSIONS[SANDBOX_NAME] = {**SESSIONS.get(SANDBOX_NAME, {}), "status": "running"}
    except modal_exc.NotFoundError:
        SESSIONS[SANDBOX_NAME] = {"status": "missing"}
    except Exception:
        logging.getLogger(__name__).exception("Unexpected error cleaning up sessions")


def get_or_start_background_sandbox() -> tuple[modal.Sandbox, str]:
    """Return a running background sandbox and its encrypted service URL.

    Starts a daemonized sandbox running `uvicorn agent_sandbox.controllers.controller:app` if one is
    not already available, then discovers its encrypted tunnel URL on port
    8001. The function blocks until the `/health_check` endpoint responds.

    Returns:
        A pair of `(sandbox, service_url)`.
    """
    global SANDBOX, SERVICE_URL

    # STEP 1: Check if we already have a connection in this worker's memory
    if SANDBOX is not None and SERVICE_URL:
        return SANDBOX, SERVICE_URL

    # -------------------------------------------------------------------------
    # STEP 2: Try to find an EXISTING sandbox by name
    # -------------------------------------------------------------------------
    # Modal sandboxes can be given names. This allows multiple workers (or even
    # separate Modal function invocations) to discover and reuse the same
    # long-running sandbox. This is key to the "persistent service" pattern.
    # -------------------------------------------------------------------------
    try:
        sb = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)
        tunnels = sb.tunnels()
        if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
            SANDBOX = sb
            SERVICE_URL = tunnels[SERVICE_PORT].url
            _wait_for_service(SERVICE_URL)
            return SANDBOX, SERVICE_URL
    except Exception:
        pass  # Sandbox doesn't exist or isn't accessible; we'll create a new one

    # -------------------------------------------------------------------------
    # STEP 3: Create a NEW sandbox
    # -------------------------------------------------------------------------
    # If no existing sandbox was found, create one. This runs uvicorn inside
    # an isolated container with its own filesystem, network, and resources.
    # -------------------------------------------------------------------------
    svc_vol = _get_persist_volume()
    try:
        SANDBOX = modal.Sandbox.create(
            # Command to run inside the sandbox (uvicorn starts our FastAPI app)
            "uvicorn",
            "agent_sandbox.controllers.controller:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(SERVICE_PORT),
            # MODAL-SPECIFIC PARAMETERS EXPLAINED:
            app=app,  # Associates sandbox with this Modal App
            image=agent_image,  # Container image with all dependencies
            secrets=agent_secrets,  # Inject secrets (API keys) into environment
            workdir="/root/app",  # Working directory inside container
            name=SANDBOX_NAME,  # Named sandbox enables discovery via from_name()
            # encrypted_ports: Makes these ports accessible via Modal's secure tunnels.
            # Without this, the ports would only be accessible inside the sandbox.
            # Modal creates HTTPS URLs that tunnel traffic to these internal ports.
            # Supports multiple ports for multi-service architectures (API + frontend).
            encrypted_ports=_settings.service_ports,
            # volumes: Mount a Modal Volume at /data for persistent storage.
            # Files written here survive sandbox restarts (but only after termination).
            volumes={"/data": svc_vol},
            # Lifecycle settings:
            timeout=_settings.sandbox_timeout,  # Max lifetime (default: 12 hours)
            idle_timeout=_settings.sandbox_idle_timeout,  # Shutdown after idle (default: 10 min)
            **_sandbox_resource_kwargs(),
            verbose=True,
        )
    except modal_exc.AlreadyExistsError:
        SANDBOX = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)

    # Optional: set tags after creation (useful for filtering in Modal dashboard)
    SANDBOX.set_tags({"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)})

    # -------------------------------------------------------------------------
    # STEP 4: Discover the tunnel URL (polling loop)
    # -------------------------------------------------------------------------
    # Modal's encrypted_ports feature creates a secure tunnel to the sandbox.
    # However, the tunnel URL isn't immediately available - Modal needs a moment
    # to provision it. We poll `sandbox.tunnels()` until the URL appears.
    #
    # The returned URL looks like: https://xxxx.modal.run
    # This URL is publicly accessible and routes to port 8001 inside the sandbox.
    # -------------------------------------------------------------------------
    SERVICE_URL = None
    deadline = time.time() + 30  # 30-second timeout for tunnel discovery
    while time.time() < deadline:
        tunnels = SANDBOX.tunnels()
        if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
            SERVICE_URL = tunnels[SERVICE_PORT].url
            break
        time.sleep(0.5)

    if not SERVICE_URL:
        raise RuntimeError("Failed to start background sandbox or get service URL")

    _wait_for_service(SERVICE_URL)
    try:
        SESSIONS[SANDBOX_NAME] = {
            "id": SANDBOX.object_id,
            "url": SERVICE_URL,
            "volume": PERSIST_VOL_NAME,
            "created_at": int(time.time()),
            "tags": {"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)},
            "status": "running",
        }
    except modal_exc.Error as e:
        logging.getLogger(__name__).warning(
            "Failed to persist session metadata to Modal Dict: %s", e
        )
    except Exception:
        logging.getLogger(__name__).exception("Unexpected error persisting session metadata")

    return SANDBOX, SERVICE_URL


async def _wait_for_service_aio(url: str, timeout: int = 60, path: str = "/health_check") -> None:
    """Async version of _wait_for_service.

    Args:
        url: Base URL of the service.
        timeout: Maximum time to wait in seconds.
        path: Health check path to append to URL.

    Raises:
        TimeoutError: If the service does not become healthy in time.
    """
    check_url = f"{url.rstrip('/')}{path}"
    deadline = anyio.current_time() + timeout
    async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=2.0)) as client:
        while anyio.current_time() < deadline:
            try:
                r = await client.get(check_url)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            await anyio.sleep(1)
    raise TimeoutError(f"Service {check_url} did not become available within {timeout} seconds")


async def get_or_start_background_sandbox_aio() -> tuple[modal.Sandbox, str]:
    """Async version of get_or_start_background_sandbox.

    Returns:
        A pair of `(sandbox, service_url)`.
    """
    global SANDBOX, SERVICE_URL

    if SANDBOX and SERVICE_URL:
        return SANDBOX, SERVICE_URL

    # Attempt global reuse by name across workers/processes
    try:
        sb = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)
        # Poll tunnels until URL appears (mirrors sync behavior)
        deadline = anyio.current_time() + 30
        url = None
        while anyio.current_time() < deadline:
            tunnels = await sb.tunnels.aio()
            if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
                url = tunnels[SERVICE_PORT].url
                break
            await anyio.sleep(0.5)
        if url:
            SANDBOX, SERVICE_URL = sb, url
            await _wait_for_service_aio(SERVICE_URL)
            return SANDBOX, SERVICE_URL
    except Exception:
        pass

    # Create with persistent volume
    svc_vol = _get_persist_volume()
    try:
        SANDBOX = await modal.Sandbox.create.aio(
            "uvicorn",
            "agent_sandbox.controllers.controller:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(SERVICE_PORT),
            app=app,
            image=agent_image,
            secrets=agent_secrets,
            workdir="/root/app",
            name=SANDBOX_NAME,
            encrypted_ports=_settings.service_ports,
            volumes={"/data": svc_vol},
            timeout=_settings.sandbox_timeout,
            idle_timeout=_settings.sandbox_idle_timeout,
            **_sandbox_resource_kwargs(),
            verbose=True,
        )
    except modal_exc.AlreadyExistsError:
        SANDBOX = await modal.Sandbox.from_name.aio("test-sandbox", SANDBOX_NAME)

    # Optional: set tags after creation
    await SANDBOX.set_tags.aio(
        {"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)}
    )

    # Poll tunnels until URL appears
    deadline = anyio.current_time() + 30
    SERVICE_URL = None
    while anyio.current_time() < deadline:
        tunnels = await SANDBOX.tunnels.aio()
        if SERVICE_PORT in tunnels and getattr(tunnels[SERVICE_PORT], "url", None):
            SERVICE_URL = tunnels[SERVICE_PORT].url
            break
        await anyio.sleep(0.5)

    if not SERVICE_URL:
        raise RuntimeError("Failed to start background sandbox or get service URL")

    await _wait_for_service_aio(SERVICE_URL)

    # Persist session metadata
    try:
        SESSIONS[SANDBOX_NAME] = {
            "id": SANDBOX.object_id,
            "url": SERVICE_URL,
            "volume": PERSIST_VOL_NAME,
            "created_at": int(time.time()),
            "tags": {"role": "service", "app": "test-sandbox", "port": str(SERVICE_PORT)},
            "status": "running",
        }
    except Exception:
        pass

    return SANDBOX, SERVICE_URL


@app.cls(
    image=agent_image,
    secrets=agent_secrets,
    volumes={"/data": _get_persist_volume()},
    enable_memory_snapshot=_settings.enable_memory_snapshot,
    **_function_runtime_kwargs(include_autoscale=False),
)
class AgentRunner:
    """Class-based agent runner with lifecycle hooks and optional memory snapshots.

    Memory Snapshot Lifecycle (when enable_memory_snapshot=True):
        1. First cold start: _snapshot_setup() runs and Modal captures memory state
        2. Subsequent starts: Container restores from snapshot, _post_restore() runs
        3. On termination: _cleanup() releases resources

    This pattern moves heavy initialization (MCP servers, tool registry) into the
    snapshot, dramatically reducing cold start latency for subsequent invocations.

    See: https://modal.com/docs/guide/memory-snapshot
    """

    system_prompt: str = modal.parameter(default=SYSTEM_PROMPT)

    @modal.enter(snap=True)
    def _snapshot_setup(self) -> None:
        """Initialize agent options and capture in memory snapshot.

        snap=True means this runs BEFORE the snapshot is taken. The initialized
        _options object will be serialized into the snapshot and restored on
        subsequent container starts, avoiding re-initialization overhead.
        """
        from agent_sandbox.agents.loop import build_agent_options

        self._options = build_agent_options(
            system_prompt=self.system_prompt,
            max_turns=_settings.agent_max_turns,
            provider_config=_settings.agent_provider_options,
        )

    @modal.enter(snap=False)
    def _post_restore(self) -> None:
        """Post-restore initialization after snapshot restore.

        snap=False means this runs AFTER restoring from snapshot. Used to
        reinitialize any state that can't be serialized (e.g., network connections).
        Also serves as fallback if snapshot wasn't taken or is corrupted.
        """
        if getattr(self, "_options", None) is None:
            from agent_sandbox.agents.loop import build_agent_options

            self._options = build_agent_options(
                system_prompt=self.system_prompt,
                max_turns=_settings.agent_max_turns,
                provider_config=_settings.agent_provider_options,
            )

    @modal.exit()
    def _cleanup(self) -> None:
        """Release resources when container shuts down."""
        self._options = None

    @modal.method()
    def run(self, question: str = DEFAULT_QUESTION) -> None:
        """Execute an agent query and stream responses to stdout."""
        import anyio

        from agent_sandbox.providers import get_provider

        async def _run() -> None:
            provider = get_provider(_settings.agent_provider)
            async with provider.create_client(options=self._options) as client:
                await client.query(question)
                async for msg in client.receive_response():
                    print(provider.serialize_message(msg))

        anyio.run(_run)


@app.function(
    image=agent_image,
    secrets=agent_secrets,
    volumes={"/data": _get_persist_volume()},
    **_function_runtime_kwargs(include_autoscale=False),
)
def run_agent_remote(question: str = DEFAULT_QUESTION) -> None:
    """Run the agent once in a short-lived Modal function.

    This is useful for synchronous, on-demand runs. For long-running, low-latency
    serving, prefer the background sandbox pattern used by `http_app`.

    Args:
        question: Natural-language query to send to the agent.
    """
    AgentRunner().run.remote(question)


@app.function(image=agent_image, secrets=agent_secrets, timeout=600)
def load_test(num_queries: int = 10, question: str = DEFAULT_QUESTION) -> dict:
    """Run parallel queries to test scaling behavior.

    Spawns multiple agent queries in parallel using Modal's distributed execution
    and measures throughput. Useful for validating autoscaling configuration
    and measuring system performance under load.

    Usage:
        modal run -m agent_sandbox.app::load_test --num-queries 10
        modal run -m agent_sandbox.app::load_test --num-queries 100 --question "Hello"

    Args:
        num_queries: Number of parallel queries to spawn.
        question: Query to send to each agent instance.

    Returns:
        Dict with load test results:
            - total_queries: Number of queries executed
            - duration_seconds: Total time taken
            - throughput_per_second: Queries completed per second
    """
    start = time.time()

    # Spawn queries in parallel using Modal's distributed execution
    handles = [run_agent_remote.spawn(question) for _ in range(num_queries)]

    # Wait for all to complete
    for h in handles:
        h.get()

    duration = time.time() - start
    return {
        "total_queries": num_queries,
        "duration_seconds": round(duration, 2),
        "throughput_per_second": round(num_queries / duration, 3),
    }


@app.function(
    image=agent_image,
    secrets=agent_secrets,
    volumes={"/data": _get_persist_volume()},
    schedule=_job_queue_schedule(),
    **_function_runtime_kwargs(include_autoscale=False),
)
def process_job_queue() -> None:
    """Process queued agent jobs from JOB_QUEUE and persist results.

    Runs on a cron schedule (job_queue_cron setting) or can be invoked directly.
    Processes up to max_jobs_per_run jobs per invocation to bound runtime.

    Job Processing Flow:
        1. Pull job from JOB_QUEUE (2s timeout per poll)
        2. Check if job was canceled (skip if so)
        3. Increment attempt counter
        4. Set status to "running"
        5. Forward query to background sandbox service
        6. Update status to "complete" or "failed" with result/error
    """
    settings = Settings()
    jobs_processed = 0
    max_jobs = settings.max_jobs_per_run

    while True:
        # Respect per-run job limit to bound execution time
        if max_jobs is not None and jobs_processed >= max_jobs:
            break
        # Non-blocking poll with 2s timeout - exit loop if queue empty
        job = JOB_QUEUE.get(timeout=2)
        if job is None:
            break
        job_id = job.get("job_id")
        question = job.get("question")
        if not job_id or not question:
            continue
        # Respect cancellation before processing
        if should_skip_job(job_id):
            update_job(job_id, {"status": "canceled"})
            continue
        bump_attempts(job_id)
        update_job(job_id, {"status": "running"})
        try:
            sb, url = get_or_start_background_sandbox()
            headers = {}
            if settings.enforce_connect_token:
                creds = sb.create_connect_token(user_metadata={"job_id": job_id})
                headers = {"Authorization": f"Bearer {creds.token}"}
            r = httpx.post(
                f"{url.rstrip('/')}/query",
                json={"question": question},
                headers=headers,
                timeout=httpx.Timeout(120.0, connect=30.0),
            )
            r.raise_for_status()
            update_job(job_id, {"status": "complete", "result": r.json()})
        except Exception as exc:
            update_job(job_id, {"status": "failed", "error": str(exc)})
        jobs_processed += 1


@app.function(
    image=agent_image,
    secrets=agent_secrets,
    **_retry_kwargs(),
)
def terminate_service_sandbox() -> dict:
    """Terminate the background sandbox to flush writes to the volume.

    Sandbox writes are synced when the sandbox terminates. If volume commits are enabled
    (via `volume_commit_interval`), writes may already be persisted without termination.
    Call this function after the agent has created files to ensure they are persisted.

    Returns:
        Dict with termination status
    """
    global SANDBOX
    try:
        sb, _ = get_or_start_background_sandbox()
        sb.terminate()
        SANDBOX = None  # Clear global so a new one will be created on next request
        return {"ok": True, "message": "Sandbox terminated, writes flushed to volume"}
    except modal_exc.NotFoundError as e:
        return {
            "ok": False,
            "error": "Sandbox not found",
            "detail": str(e),
            "type": "NotFoundError",
        }
    except modal_exc.SandboxTerminatedError:
        return {
            "ok": False,
            "error": "Sandbox already terminated",
            "type": "SandboxTerminatedError",
        }
    except modal_exc.TimeoutError as e:
        return {"ok": False, "error": "Sandbox termination timed out", "type": e.__class__.__name__}
    except modal_exc.Error as e:
        return {"ok": False, "error": str(e), "type": e.__class__.__name__}
    except Exception:
        logging.getLogger(__name__).exception("Unexpected error terminating sandbox")
        return {"ok": False, "error": "Unexpected error", "type": "UnexpectedException"}


@app.function(image=agent_image, secrets=agent_secrets, timeout=300, **_retry_kwargs())
def snapshot_service() -> dict:
    """Capture the sandbox filesystem as a reusable Modal Image.

    Creates a snapshot of the current sandbox filesystem state, which can be
    used to create new sandboxes with the same files/configuration. Useful for
    capturing agent-installed tools or downloaded artifacts.

    The snapshot metadata is persisted to SESSIONS for later retrieval.

    Returns:
        Dict with snapshot info: image_id (Modal Image ID), ts (timestamp),
        and base (sandbox name the snapshot was taken from).

    See: https://modal.com/docs/guide/sandbox#filesystem-snapshots
    """
    sb, _ = get_or_start_background_sandbox()
    img = sb.snapshot_filesystem()
    info = {"image_id": img.object_id, "ts": int(time.time()), "base": SANDBOX_NAME}
    try:
        SESSIONS[f"{SANDBOX_NAME}-snapshot"] = info
    except modal_exc.Error as e:
        logging.getLogger(__name__).warning(
            "Failed to persist snapshot metadata to Modal Dict: %s", e
        )
    except Exception:
        logging.getLogger(__name__).exception("Unexpected error persisting snapshot metadata")
    return info


# For 'modal run' command
@app.local_entrypoint()
def main():
    """Local entry point for `modal run -m agent_sandbox.app` during development.

    Spins up a short-lived sandbox, executes `agent_sandbox.agents.loop`, streams logs, and
    terminates the sandbox. Prefer `modal serve -m agent_sandbox.app` to keep endpoints and
    hot code reloading during development.
    """
    sb = modal.Sandbox.create(
        app=app,
        image=agent_image,
        secrets=agent_secrets,
        workdir="/root/app",
        timeout=60 * 10,  # 10 minutes
        **_sandbox_resource_kwargs(),
        verbose=True,
    )

    p = sb.exec("python", "-m", "agent_sandbox.agents.loop", timeout=60)

    print("=== STDOUT ===")
    for line in p.stdout:
        print(line, end="")
    print("\n=== STDERR ===")
    for line in p.stderr:
        print(line, end="")

    sb.terminate()

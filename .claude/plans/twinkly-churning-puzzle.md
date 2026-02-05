# Modal Dev Sandbox Error Fix

## Root Cause

Named sandboxes (`Sandbox.from_name()`) **require a deployed app**. This is explicitly stated in Modal documentation:

> "Note that the associated app must be a deployed app."

When running `modal serve`, the app `"test-sandbox"` is ephemeral, not deployed. The error chain:

1. `Sandbox.create(name="svc-runner-8001")` raises `AlreadyExistsError` (sandbox exists from previous run)
2. Recovery calls `Sandbox.from_name("test-sandbox", "svc-runner-8001")`
3. Fails with `NotFoundError: App test-sandbox not found in environment main`
4. Code re-raises: `Sandbox 'svc-runner-8001' already exists but cannot be looked up in dev mode`

## Solution: Use `App.lookup()` for a Dedicated Deployed App

Use `modal.App.lookup("sandbox-manager-app", create_if_missing=True)` to get/create a **deployed** app for sandbox management. This works in both `modal serve` (dev) and `modal deploy` (prod).

Key insight: `App.lookup(name, create_if_missing=True)` always creates a deployed app, regardless of how the main app is running.

## Code Changes

### File: `agent_sandbox/app.py`

#### 1. Add constant (after line 2513)

```python
# Line ~2514: Add after PERSIST_VOL_NAME
# Dedicated deployed app name for sandbox management.
# Using App.lookup() ensures the app is deployed (not ephemeral), which is
# required for named sandboxes to work with Sandbox.from_name().
SANDBOX_APP_NAME = "sandbox-manager-app"
```

#### 2. Sync function: `get_or_start_background_sandbox()`

**Line 2974** - First lookup:
```python
# Before:
sb = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)

# After:
sandbox_app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
sb = modal.Sandbox.from_name(SANDBOX_APP_NAME, SANDBOX_NAME)
```

**Lines 3091-3126** - Create and error handler:
```python
# Before (lines 3091-3126):
    try:
        SANDBOX = modal.Sandbox.create(
            ...
            app=app,  # <-- ephemeral app
            ...
        )
    except modal_exc.AlreadyExistsError:
        try:
            SANDBOX = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)
        except modal_exc.NotFoundError:
            raise modal_exc.AlreadyExistsError(...)

# After:
    sandbox_app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
    try:
        SANDBOX = modal.Sandbox.create(
            ...
            app=sandbox_app,  # <-- deployed app
            ...
        )
    except modal_exc.AlreadyExistsError:
        # With a deployed app, from_name works in both dev and prod
        SANDBOX = modal.Sandbox.from_name(SANDBOX_APP_NAME, SANDBOX_NAME)
```

#### 3. Async function: `get_or_start_background_sandbox_aio()`

**Line 3221** - First lookup:
```python
# Before:
sb = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)

# After:
sandbox_app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
sb = modal.Sandbox.from_name(SANDBOX_APP_NAME, SANDBOX_NAME)
```

**Lines 3328-3354** - Create and error handler:
```python
# Before (lines 3328-3354):
    try:
        SANDBOX = await modal.Sandbox.create.aio(
            ...
            app=app,
            ...
        )
    except modal_exc.AlreadyExistsError:
        try:
            SANDBOX = await modal.Sandbox.from_name.aio("test-sandbox", SANDBOX_NAME)
        except modal_exc.NotFoundError:
            raise modal_exc.AlreadyExistsError(...)

# After:
    sandbox_app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
    try:
        SANDBOX = await modal.Sandbox.create.aio(
            ...
            app=sandbox_app,
            ...
        )
    except modal_exc.AlreadyExistsError:
        SANDBOX = await modal.Sandbox.from_name.aio(SANDBOX_APP_NAME, SANDBOX_NAME)
```

#### 4. Cron job: `cleanup_sessions()` (line 2577)

```python
# Before:
sb = modal.Sandbox.from_name("test-sandbox", SANDBOX_NAME)

# After:
try:
    sandbox_app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
    sb = modal.Sandbox.from_name(SANDBOX_APP_NAME, SANDBOX_NAME)
except Exception:
    ...
```

#### 5. Warm pool: `_create_warm_sandbox_sync()` (lines 2607-2625)

```python
# Before (line 2614):
app=app,

# After:
sandbox_app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
# ... in Sandbox.create():
app=sandbox_app,
```

## Summary of Changes

| Location | Line(s) | Change |
|----------|---------|--------|
| Module constant | ~2514 | Add `SANDBOX_APP_NAME = "sandbox-manager-app"` |
| Sync first lookup | 2974 | Use `App.lookup()` + change app name |
| Sync create | 3100 | Change `app=app` to `app=sandbox_app` |
| Sync error handler | 3119-3126 | Simplify - remove nested try/except |
| Async first lookup | 3221 | Use `App.lookup()` + change app name |
| Async create | 3335 | Change `app=app` to `app=sandbox_app` |
| Async error handler | 3347-3354 | Simplify - remove nested try/except |
| Cron cleanup | 2577 | Use `App.lookup()` + change app name |
| Warm pool create | 2614 | Use `App.lookup()` + change app reference |

## Verification Steps

### 1. Clean state test
```bash
# Kill any existing sandbox
modal sandbox list | grep svc-runner-8001 && modal sandbox terminate svc-runner-8001

# Start dev server
modal serve -m agent_sandbox.app
```
Expected: No `NotFoundError` or `AlreadyExistsError` in logs.

### 2. End-to-end with Cloudflare Worker
```bash
# Terminal 1: Modal dev server
modal serve -m agent_sandbox.app

# Terminal 2: Cloudflare Worker
cd cloudflare-control-plane && npm run dev

# Terminal 3: Test query
curl -sS http://localhost:8787/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"hello"}'
```
Expected: 200 response with valid JSON.

### 3. Hot-reload recovery test
```bash
# With modal serve running, trigger hot-reload (edit app.py)
# Make another request - should reconnect to existing sandbox
```

### 4. Production compatibility
```bash
modal deploy -m agent_sandbox.deploy
# Verify endpoints work as before
```

## Critical Files

- `agent_sandbox/app.py` - Main changes (sandbox management functions)
- `agent_sandbox/config/settings.py` - No changes needed (sandbox_name source)

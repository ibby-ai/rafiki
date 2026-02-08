# Modal Sandbox Documentation Research: Dev Mode vs Deployed Apps

## Summary of Findings

Based on research from Modal's official documentation, here are the key findings regarding the errors you're experiencing with `Sandbox.from_name()` in dev mode.

---

## Key Documentation Excerpts

### 1. Named Sandboxes Require Deployed Apps

From [Modal Sandbox Guide](https://modal.com/docs/guide/sandboxes):

> **Named Sandboxes**
>
> You can assign a name to a Sandbox when creating it. Each name must be unique within an app - only one running Sandbox can use a given name at a time. **Note that the associated app must be a deployed app.** Once a Sandbox completely stops running, its name becomes available for reuse.

This is the critical documentation explaining your error. **Named sandboxes explicitly require a deployed app** - they cannot work with ephemeral apps created via `modal serve` or `modal run`.

### 2. `Sandbox.from_name()` Only Works with Deployed Apps

From [modal.Sandbox Reference](https://modal.com/docs/reference/modal.Sandbox):

```python
@staticmethod
def from_name(
    app_name: str,
    name: str,
    *,
    environment_name: Optional[str] = None,
    client: Optional[_Client] = None,
) -> "_Sandbox":
```

> Get a running Sandbox by name from a **deployed App**.
>
> Raises a `modal.exception.NotFoundError` if no running sandbox is found with the given name.
>
> A Sandbox's name is the `name` argument passed to `Sandbox.create`.

### 3. `Sandbox.create()` vs `Sandbox.from_name()` Comparison

| Feature | `Sandbox.create()` | `Sandbox.from_name()` |
|---------|-------------------|----------------------|
| Creates new sandbox | Yes | No (lookup only) |
| Works with ephemeral apps | Yes | **No** |
| Works with deployed apps | Yes | Yes |
| Requires `name` parameter | Optional | Required |
| Named sandbox lookup | No | Yes |

### 4. `App.lookup()` Creates Deployed Apps

From [modal.App Reference](https://modal.com/docs/reference/modal.App):

```python
@staticmethod
def lookup(
    name: str,
    *,
    client: Optional[_Client] = None,
    environment_name: Optional[str] = None,
    create_if_missing: bool = False,
) -> "_App":
```

> Look up an App with a given name, creating a new App if necessary.
>
> Note that **Apps created through this method will be in a deployed state**, but they will not have any associated Functions or Classes.

### 5. `modal serve` Creates Ephemeral Apps

From [modal serve Reference](https://modal.com/docs/reference/cli/serve):

> Run a web endpoint(s) associated with a Modal app and hot-reload code.
>
> Modal-generated URLs will have a **-dev suffix** appended to them when running with `modal serve`.

From [Apps Guide](https://modal.com/docs/guide/apps):

> An **ephemeral App** is created when you use the `modal run` CLI command, or the `app.run` method. This creates a temporary App that only exists for the duration of your script.
>
> A **deployed App** is created using the `modal deploy` CLI command. The App is persisted indefinitely until you stop it.

---

## Error Analysis

### Error 1: `NotFoundError: App test-sandbox not found in environment main`

**Cause:** `Sandbox.from_name("test-sandbox", "svc-runner-8001")` is looking for a **deployed** app named "test-sandbox", but when running `modal serve`, the app is ephemeral and doesn't exist as a persistent deployed app.

### Error 2: `AlreadyExistsError: Sandbox 'svc-runner-8001' already exists but cannot be looked up in dev mode`

**Cause:** A sandbox with the name `svc-runner-8001` was previously created (perhaps from a prior run), but named sandbox lookup (`from_name`) is not supported in dev/ephemeral mode. The sandbox exists but cannot be retrieved by name.

---

## Recommended Patterns for Development

### Option 1: Use `Sandbox.from_id()` Instead of `from_name()` (Recommended for Dev)

Store the sandbox's `object_id` after creation and use it for lookup:

```python
# Global storage for sandbox reference
SANDBOX_ID: Optional[str] = None

def get_or_start_background_sandbox():
    global SANDBOX_ID

    # Try to reconnect using object_id if we have one
    if SANDBOX_ID:
        try:
            sb = modal.Sandbox.from_id(SANDBOX_ID)
            # Verify it's still running
            if sb.poll() is None:
                return sb
        except Exception:
            pass

    # Create new sandbox (without name in dev mode)
    sb = modal.Sandbox.create(
        # ... other params ...
        # Don't use name= in dev mode
        app=app,
    )
    SANDBOX_ID = sb.object_id
    return sb
```

### Option 2: Conditional Naming Based on Deployment Context

Detect if running in deployed vs dev mode and adjust behavior:

```python
import os

def is_deployed() -> bool:
    """Check if we're running in a deployed context."""
    # MODAL_IS_REMOTE is set inside Modal containers
    # You may need to check other indicators
    return os.environ.get("MODAL_DEPLOYMENT_NAME") is not None

def get_or_start_background_sandbox():
    if is_deployed():
        # Use named sandbox with from_name for deployed apps
        try:
            return modal.Sandbox.from_name("my-deployed-app", "svc-runner-8001")
        except modal.exception.NotFoundError:
            pass

        # Create with name for deployed apps
        deployed_app = modal.App.lookup("my-deployed-app", create_if_missing=True)
        return modal.Sandbox.create(
            name="svc-runner-8001",
            app=deployed_app,
            # ... other params ...
        )
    else:
        # Dev mode: use anonymous sandbox with object_id tracking
        return modal.Sandbox.create(
            # No name parameter
            app=app,
            # ... other params ...
        )
```

### Option 3: Always Use a Separate Deployed App for Sandboxes

Create a dedicated deployed app just for sandboxes, separate from your serve app:

```python
# Get or create a deployed app specifically for sandboxes
sandbox_app = modal.App.lookup("sandbox-manager", create_if_missing=True)

def get_or_start_background_sandbox():
    sandbox_name = "svc-runner-8001"

    try:
        # This works because sandbox_app is deployed via App.lookup
        return modal.Sandbox.from_name("sandbox-manager", sandbox_name)
    except modal.exception.NotFoundError:
        return modal.Sandbox.create(
            name=sandbox_name,
            app=sandbox_app,
            # ... other params ...
        )
```

**Note:** This approach uses `App.lookup()` which creates a deployed app, so named sandboxes will work. However, be aware that multiple `modal serve` sessions could conflict.

### Option 4: Use Unique Sandbox Names per Dev Session

Generate unique sandbox names to avoid conflicts:

```python
import uuid

DEV_SESSION_ID = str(uuid.uuid4())[:8]

def get_sandbox_name():
    if is_deployed():
        return "svc-runner-8001"
    else:
        return f"svc-runner-8001-dev-{DEV_SESSION_ID}"
```

---

## Best Practices Summary

1. **For Production (`modal deploy`):**
   - Use named sandboxes with `Sandbox.create(name=...)` and `Sandbox.from_name()`
   - The deployed app persists, so named sandbox lookup works correctly

2. **For Development (`modal serve`):**
   - **Avoid** `Sandbox.from_name()` - it won't work with ephemeral apps
   - Use `Sandbox.from_id()` with stored object IDs for reconnection
   - Or create anonymous sandboxes (no `name` parameter) and track them differently
   - Or use `App.lookup()` to create a separate deployed app for sandboxes

3. **Handle the `AlreadyExistsError` gracefully:**
   - If you must use named sandboxes in dev, catch `AlreadyExistsError` and either:
     - Terminate the existing sandbox first
     - Generate a unique name
     - Skip creation and try `from_id` if you stored the ID

---

## Implementation Recommendation

For your `agent-sandbox-starter` project, I recommend **Option 3** (separate deployed app for sandboxes) combined with proper error handling:

```python
# In modal_backend/main.py

import modal
from modal.exception import NotFoundError, AlreadyExistsError

# Dedicated deployed app for sandbox management
SANDBOX_APP_NAME = "agent-sandbox-manager"

def get_or_start_background_sandbox():
    global SANDBOX, SERVICE_URL

    if SANDBOX is not None and SERVICE_URL is not None:
        try:
            if SANDBOX.poll() is None:
                return SERVICE_URL
        except Exception:
            pass

    # Use App.lookup to get/create a DEPLOYED app for sandboxes
    sandbox_app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
    sandbox_name = settings.sandbox_name  # e.g., "svc-runner-8001"

    # Try to find existing running sandbox
    try:
        SANDBOX = modal.Sandbox.from_name(SANDBOX_APP_NAME, sandbox_name)
        # ... get tunnel URL ...
        return SERVICE_URL
    except NotFoundError:
        pass  # Sandbox doesn't exist, create it

    # Create new sandbox
    try:
        SANDBOX = modal.Sandbox.create(
            name=sandbox_name,
            app=sandbox_app,
            # ... other configuration ...
        )
    except AlreadyExistsError:
        # Race condition or stale sandbox - try from_name again
        SANDBOX = modal.Sandbox.from_name(SANDBOX_APP_NAME, sandbox_name)

    # ... get tunnel URL and wait for service ...
    return SERVICE_URL
```

This approach:
- Works in both dev (`modal serve`) and production (`modal deploy`)
- Uses a dedicated deployed app (`App.lookup`) for sandbox management
- Properly handles the `from_name` lookup pattern
- Handles race conditions with `AlreadyExistsError`

---

## Documentation Sources

- Sandbox Guide: https://modal.com/docs/guide/sandboxes
- Sandbox Reference: https://modal.com/docs/reference/modal.Sandbox
- App Reference: https://modal.com/docs/reference/modal.App
- Apps Guide: https://modal.com/docs/guide/apps
- modal serve Reference: https://modal.com/docs/reference/cli/serve
- Developing and Debugging: https://modal.com/docs/guide/developing-debugging

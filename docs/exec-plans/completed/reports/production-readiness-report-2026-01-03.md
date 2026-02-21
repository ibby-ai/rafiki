## Production Readiness Report (Jan 3, 2026)

### Scope
- Disk allocation guidance + guardrails for Modal ephemeral disk
- Session resumption over HTTP (`/query` with `session_key` / `session_id`)
- Max turns enforcement for Claude Agent SDK runs
- End-to-end validation via Modal runs and HTTP tests

### What We Achieved
- **Session resumption over HTTP works**:
  - `POST /query` with `session_key` returns a `session_id`.
  - A second `POST /query` with that `session_id` resumes the same SDK session and recalls prior context (confirmed by the “ALPHA” check).
- **Safe disk configuration behavior**:
  - Ephemeral disk validation now accepts any positive MiB value up to Modal’s 3.0 TiB max, avoiding invalid “minimum” assumptions.
  - Docs updated to reflect the correct limit.
- **Max turns enforcement**:
  - Agent runs are capped via `agent_max_turns` and passed into `ClaudeAgentOptions` consistently across entrypoints.
- **Documentation improvements**:
  - README and `docs/references/configuration.md` updated with session resumption and disk configuration guidance.

### Obstacles Encountered (and How We Unblocked)
1) **Modal Dict / Volume AuthError in dev sandboxes**
   - **Symptom**: `AuthError: Token missing` when trying to reload/commit volumes or access the session store Dict from the sandbox.
   - **Root cause**: The sandbox runtime doesn’t have Modal credentials for Dict/Volume operations in this dev context.
   - **Fix/workaround**: Implemented a fallback in-memory session cache when the Dict is unavailable. This keeps session resumption functional for dev testing without requiring Modal credentials in the sandbox.

2) **Sandbox name collisions (`AlreadyExistsError`)**
   - **Symptom**: `modal.exception.AlreadyExistsError` when concurrent requests attempt to create the named sandbox.
   - **Root cause**: Multiple workers racing to create the same named sandbox.
   - **Fix**: Catch `AlreadyExistsError` and reattach via `modal.Sandbox.from_name` so concurrent requests reuse the existing sandbox instead of failing.

3) **`NotFoundError` in dev for `Sandbox.from_name`**
   - **Symptom**: `App test-sandbox not found in environment main` when calling `Sandbox.from_name` under `modal serve`.
   - **Root cause**: `from_name` requires a deployed app; ephemeral dev apps can’t always be looked up by name.
   - **Validation workaround**: Run the dev server with a single worker to avoid the fallback `from_name` path during creation (`MIN_CONTAINERS=1 MAX_CONTAINERS=1`). For production, use `modal deploy` so `from_name` is valid.

4) **Session ID extraction and JSON payload issues**
   - **Symptom**: Session ID parsing failed and JSON errors appeared during curl tests.
   - **Root cause**: `session_id` extraction script wasn’t receiving the response payload, and the second request body had a malformed JSON string.
   - **Fix**: Parse the response via `python -c` piping from `printf` and ensure proper JSON construction in the second request.

### Verification Summary
- **HTTP session resumption**: Confirmed by “ALPHA” recall over `/query` using `session_key` → `session_id`.
- **Modal run checks**:
  - `modal run -m modal_backend.main`
  - `modal run -m modal_backend.main::run_agent_remote --question "health check"`
- **Linting/formatting**:
  - `uv run ruff check --fix .`
  - `uv run ruff format .`

### Commands Used (for traceability)
- `uv run ruff check --fix .`
- `uv run ruff format .`
- `modal run -m modal_backend.main`
- `modal run -m modal_backend.main::run_agent_remote --question "health check"`
- `MIN_CONTAINERS=1 MAX_CONTAINERS=1 modal serve -m modal_backend.main`
- `curl -X POST https://<dev-url>/query` with:
  - `{"question":"Remember the codeword ALPHA.","session_key":"resume-demo"}`
  - `{"question":"What codeword did I ask you to remember?","session_id":"<returned>"}` 

### Notes / Recommendations
- **Dev environment**: `Sandbox.from_name` can still error under `modal serve` if multiple workers are used. Either run a single worker for local testing or use `modal deploy` to enable name lookup.
- **Session store persistence**: For production, ensure Modal Dict credentials are available in the sandbox to persist session mappings across restarts; otherwise session resumption will be memory-only per container.
- **Ephemeral disk**: The new validation only rejects non-positive values or values above 3.0 TiB; adjust `sandbox_ephemeral_disk` to match actual workload needs.

---
name: modal-sdk
description: Explore Modal Python SDK source code and official documentation to understand implementation details, API patterns, and capabilities. Use when: (1) working with Sandbox, Volume, Secret, or other Modal primitives, (2) understanding function decorators and app lifecycle, (3) building container images or ASGI web endpoints, (4) using Queue/Dict for distributed state, (5) troubleshooting Modal behavior. Combines local SDK source exploration with browser-based access to Modal's official reference docs, guides, and examples.
---

# Modal SDK Source

SDK location: `.venv/lib/python*/site-packages/modal/`

## Key Files

| File | Contents |
|------|----------|
| `__init__.py` | Public exports: `App`, `Sandbox`, `Volume`, `Secret`, `Image`, `Queue`, `Dict`, `Function` |
| `app.py` | `App` class - application definition, `@app.function()`, `@app.cls()` decorators |
| `sandbox.py` | `Sandbox` - isolated execution, `create()`, `exec()`, `tunnels()` |
| `volume.py` | `Volume` - persistent storage, `commit()`, `reload()` |
| `secret.py` | `Secret` - credential management, `from_name()`, `from_dict()` |
| `image.py` | `Image` - container building, `pip_install()`, `apt_install()`, `run_commands()` |
| `queue.py` | `Queue` - distributed FIFO queue, `put()`, `get()` |
| `dict.py` | `Dict` - distributed dictionary, key-value operations |
| `_functions.py` | `Function` internals, `remote()`, `spawn()`, `map()` |
| `_runtime/asgi.py` | ASGI/FastAPI integration, `asgi_app` decorator |

## Search Patterns

```bash
# Find SDK files
Glob pattern=".venv/lib/python*/site-packages/modal/**/*.py"

# Search SDK source
Grep pattern="<query>" path=".venv/lib/python*/site-packages/modal"
```

## Common Queries

| Topic | Search Pattern |
|-------|---------------|
| Sandbox creation | `class _Sandbox\|def create` |
| Volume commit/reload | `def commit\|def reload` |
| Function decorators | `@.*function\|@.*cls` |
| Image building | `pip_install\|apt_install\|run_commands` |
| ASGI integration | `asgi_app\|web_endpoint` |
| Queue operations | `class _Queue\|def put\|def get` |
| Secret handling | `from_name\|from_dict` |
| Tunnel setup | `class Tunnel\|def tunnels` |
| GPU configuration | `class GPU\|gpu=` |

## Modal Documentation (Online)

For high-level concepts, examples, and API reference, consult Modal's official documentation using browser automation tools (e.g., agent-browser).

| URL | Contents | When to Use |
|-----|----------|-------------|
| https://modal.com/docs/reference | API Reference - class/method signatures, parameters, return types | Look up method signatures, understand API contracts |
| https://modal.com/docs/guide | Guides - conceptual explanations, tutorials, best practices | Learn concepts, follow tutorials, understand patterns |
| https://modal.com/docs/examples | Examples - complete runnable code for common tasks | Find working implementations, copy patterns |

### When to Use Docs vs Source

| Need | Use |
|------|-----|
| Method signature, parameters | Docs (Reference) |
| Conceptual understanding | Docs (Guide) |
| Working code examples | Docs (Examples) |
| Internal implementation details | SDK Source |
| Edge case behavior | SDK Source |
| Undocumented features | SDK Source |

### Browser Navigation

Use browser automation to visit docs:
1. Navigate to the appropriate docs URL
2. Use the left sidebar to find specific topics
3. Use the search bar (Cmd+K) for quick lookup

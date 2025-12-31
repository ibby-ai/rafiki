---
allowed-tools: Read, Glob, Grep, Bash(git diff:*), Bash(git log:*), Bash(git show:*), mcp__claude-in-chrome__*
description: Verify project documentation against official Modal docs
---

## Context

This command verifies that project documentation AND code implementations are accurate and up-to-date with the official Modal documentation. Use this after making changes to Modal-related code or documentation.

### Recent Changes to Verify

Files changed since last commit:
!`git diff --name-only HEAD~1 2>/dev/null || echo "No previous commit"`

Files with uncommitted changes:
!`git diff --name-only 2>/dev/null || echo "No uncommitted changes"`

Recent commits touching Modal-related files:
!`git log --oneline -5 --all -- '*.py' '*.md' 2>/dev/null || echo "No recent commits"`

### Project Files Containing Modal Code/Documentation

**Python files importing Modal:**
!`grep -rl "import modal" --include="*.py" . 2>/dev/null | head -20 || echo "No Modal imports found"`

**Documentation mentioning Modal:**
!`grep -rl -i "modal\|sandbox\|volume" --include="*.md" . 2>/dev/null | grep -v node_modules | head -20 || echo "No Modal docs found"`

### Key Modal Concepts to Verify

| Concept | How to Find | What to Verify |
|---------|-------------|----------------|
| `Sandbox.create()` | `grep -r "Sandbox.create" --include="*.py"` | `timeout`, `idle_timeout`, `encrypted_ports`, `volumes`, `name` params |
| Sandbox defaults | `grep -ri "timeout\|idle" --include="*.md"` | Modal default timeout is 5 min (not 12h), max 24 hours |
| `Sandbox.from_name()` | `grep -r "from_name" --include="*.py"` | Correct argument order: `(app_name, sandbox_name)` |
| `Sandbox.tunnels()` | `grep -r "\.tunnels" --include="*.py"` | Returns dict of port -> Tunnel objects with `.url` |
| `@modal.asgi_app()` | `grep -r "asgi_app\|web_endpoint" --include="*.py"` | Using `@modal.asgi_app()`, not deprecated `@web_endpoint` |
| `modal.Volume` | `grep -r "modal.Volume\|Volume.from" --include="*.py"` | `from_name()`, mounting syntax, persistence behavior |
| `modal.Image` | `grep -r "modal.Image" --include="*.py"` | `debian_slim()`, `pip_install()`, `apt_install()`, `run_commands()` |
| `modal.Secret` | `grep -r "modal.Secret" --include="*.py"` | `from_name()`, `required_keys` parameter |
| `modal.App` vs `Stub` | `grep -r "modal.Stub\|modal.App" --include="*.py"` | Using `modal.App`, not deprecated `modal.Stub` |

## Your Task

Use the browser automation tools to visit the following official Modal documentation URLs:

1. **https://modal.com/docs/guide** - Introduction and core concepts
2. **https://modal.com/docs/guide/sandboxes** - Sandbox lifecycle, timeouts, tunnels
3. **https://modal.com/docs/guide/apps** - Apps, Functions, entrypoints
4. **https://modal.com/docs/guide/images** - Container image building
5. **https://modal.com/docs/guide/webhooks** - Web endpoints and ASGI apps
6. **https://modal.com/docs/reference/modal.Sandbox** - Sandbox API reference

### Verification Steps

1. **Review recent changes** shown in the Context section above to understand what was modified
2. **Open browser tabs** for the relevant Modal documentation URLs
3. **For each changed file**, verify against official Modal docs:

#### Documentation Verification
- Terminology accuracy (e.g., "App" not "Stub", correct parameter names)
- Default values stated correctly (Modal's sandbox timeout default is 5 minutes, max 24 hours)
- Feature descriptions match current Modal capabilities
- Links to Modal docs are valid and not 404

#### Code Implementation Verification
- `Sandbox.create()` parameters match current API signature
- `Sandbox.tunnels()` usage matches return type (dict of port -> Tunnel with `.url`)
- `Sandbox.from_name(app_name, sandbox_name)` argument order is correct
- `@modal.asgi_app()` decorator usage (not deprecated `@web_endpoint`)
- `modal.Image` builder methods exist and are used correctly
- `modal.Volume.from_name()` and mounting syntax
- `modal.Secret.from_name()` with `required_keys` parameter
- No use of deprecated `modal.Stub` (should be `modal.App`)

4. **Compile a verification report** with findings

### Output Format

Provide your findings in this structure:

```markdown
## Modal Verification Report

### Files Reviewed
- List of files checked (from recent changes + core Modal files)

### Documentation: Accurate
| File | Section | Verified Against |
|------|---------|------------------|
| ... | ... | Modal docs URL |

### Code: Accurate
| File | Modal API Used | Verified Against |
|------|----------------|------------------|
| ... | ... | Modal docs URL |

### Issues Found
| File:Line | Type | Issue | Modal's Current API | Recommended Fix |
|-----------|------|-------|---------------------|-----------------|
| ... | doc/code | ... | ... | ... |

### Deprecated Patterns Detected
| File:Line | Deprecated | Current Alternative |
|-----------|------------|---------------------|
| ... | ... | ... |

### Recommended Next Steps
1. ...
2. ...
```

If you find issues, ask the user if they want you to fix them.

## Common Issues to Watch For

| Issue | Example | Fix |
|-------|---------|-----|
| Deprecated Stub | `modal.Stub()` | Use `modal.App()` |
| Old web decorator | `@modal.web_endpoint` | Use `@modal.fastapi_endpoint()` or `@modal.asgi_app()` |
| Wrong timeout default | "Modal default is 12 hours" | Modal default is 5 minutes, max 24 hours |
| Old tunnel API | `sb.tunnels()[0].url` | `sb.tunnels()[PORT].url` (dict keyed by port) |
| Missing create_if_missing | `Volume.from_name("x")` | Add `create_if_missing=True` if needed |

# Oracle CLI User Manual

> One-shot GPT-5.2 Pro queries with file context for deep code analysis.

## Quick Reference

```bash
# Basic usage
oracle --prompt "Your question" --file path/to/files

# With folders
oracle --prompt "Review this code" --file modal_backend/

# Multiple folders
oracle --prompt "Compare implementations" \
  --file modal_backend/api/ \
  --file modal_backend/ralph/

# Glob patterns
oracle --prompt "Review Python code" --file "modal_backend/**/*.py"

# Exclude patterns
oracle --prompt "Review code, skip tests" \
  --file "modal_backend/**/*.py" \
  --file "!**/*_test.py"
```

---

## Setup

### API Key Configuration

```bash
# OpenAI API (required for API mode)
export OPENAI_API_KEY="sk-..."

# Verify setup with dry run
oracle --dry-run --prompt "Test" --file README.md
```

### Azure OpenAI

```bash
export AZURE_OPENAI_API_KEY="..."

oracle --azure-endpoint "https://your-resource.openai.azure.com/" \
  --azure-deployment "your-deployment-name" \
  --azure-api-version "2024-02-15-preview" \
  --prompt "..." --file ...
```

### Browser Mode (No API Key)

```bash
# Uses your logged-in ChatGPT session
oracle --engine browser --prompt "Review this code" --file modal_backend/
```

---

## File & Folder Attachment

### Single File
```bash
oracle --prompt "Explain this file" --file modal_backend/main.py
```

### Single Folder
```bash
oracle --prompt "Review this module" --file modal_backend/api/
```

### Multiple Folders
```bash
# Method 1: Repeat flag
oracle --prompt "Review the codebase" \
  --file modal_backend/api/ \
  --file modal_backend/mcp_tools/ \
  --file modal_backend/ralph/

# Method 2: Space-separated
oracle --prompt "Review the codebase" \
  --file modal_backend/api/ modal_backend/mcp_tools/ modal_backend/ralph/
```

### Glob Patterns
```bash
# All Python files recursively
oracle --prompt "Review Python code" --file "modal_backend/**/*.py"

# Specific extensions
oracle --prompt "Review configs" --file "**/*.yaml" --file "**/*.toml"

# Mix files, folders, and globs
oracle --prompt "Full review" \
  --file modal_backend/ \
  --file "*.md" \
  --file pyproject.toml
```

### Exclusion Patterns
```bash
# Exclude tests
oracle --prompt "Review production code" \
  --file "modal_backend/**/*.py" \
  --file "!**/*_test.py" \
  --file "!**/tests/**"

# Exclude cache and build artifacts
oracle --prompt "Review source" \
  --file modal_backend/ \
  --file "!**/__pycache__/**" \
  --file "!**/.venv/**"
```

---

## Git Diff Analysis

### Save Diff to File

```bash
# Last N commits
git diff HEAD~5 > /tmp/recent_changes.diff

# Python files only
git diff HEAD~5 -- "*.py" > /tmp/recent_changes.diff

# Specific files
git diff HEAD~5 -- modal_backend/main.py modal_backend/jobs.py > /tmp/changes.diff

# Compare branches
git diff main..feature-branch > /tmp/pr_changes.diff

# Specific commit range
git diff abc123..def456 > /tmp/changes.diff
```

### Review Diff with Oracle

```bash
# Diff only
oracle --prompt "Review this diff for bugs and security issues" \
  --file /tmp/recent_changes.diff \
  --model gpt-5.2-pro

# Diff + codebase context (recommended)
oracle --prompt "Review this diff in context. Check for breaking changes." \
  --file /tmp/recent_changes.diff \
  --file "modal_backend/**/*.py" \
  --model gpt-5.2-pro

# Process substitution (no temp file)
oracle --prompt "Review this diff" \
  --file <(git diff HEAD~5) \
  --file modal_backend/
```

### PR Review Template

```bash
git diff main..HEAD -- "*.py" > /tmp/pr.diff

oracle --prompt "Review this PR diff for:
1. Security vulnerabilities
2. Race conditions
3. Missing error handling
4. Breaking API changes
5. Test coverage gaps" \
  --file /tmp/pr.diff \
  --file modal_backend/ \
  --model gpt-5.2-pro \
  --slug "pr-review-$(date +%Y%m%d)"
```

---

## MCP + Oracle Workflow

Combine Claude's MCP capabilities with Oracle for multi-model analysis.

### Pattern
```
Claude (MCP tools) -> gather data -> write to file -> Oracle (GPT 5.2 Pro) -> analysis
```

### Step 1: Gather Context with MCP

Ask Claude to:
1. Query database via MCP server
2. Fetch documentation via context7
3. Map symbols via serena
4. Gather API responses

### Step 2: Save MCP Context

```bash
cat > /tmp/mcp_context.md << 'EOF'
# MCP-Gathered Context

## Database Schema (from MCP query)
- users table: id, email, created_at
- sessions table: id, user_id, started_at

## Symbol Map (from Serena)
- modal_backend/main.py
  - Function: get_or_start_background_sandbox (line 45)
  - Function: get_or_start_cli_sandbox (line 89)

## API Response Samples
...
EOF
```

### Step 3: Run Oracle with Combined Context

```bash
oracle --prompt "Given this MCP-gathered context, review the codebase for:
1. Schema mismatches
2. Missing API error handling
3. Unused symbols" \
  --file /tmp/mcp_context.md \
  --file modal_backend/ \
  --model gpt-5.2-pro
```

### Example: Full MCP + Diff + Oracle Pipeline

```bash
# 1. Get diff
git diff HEAD~5 -- "*.py" > /tmp/diff.patch

# 2. Have Claude gather MCP context (symbols, references)
# Save to /tmp/mcp_context.md

# 3. Run Oracle with everything
oracle --prompt "Review this PR with full context:
- MCP context shows symbol relationships
- Diff shows what changed
- Codebase shows full implementation

Check for breaking changes and missing updates." \
  --file /tmp/mcp_context.md \
  --file /tmp/diff.patch \
  --file modal_backend/ \
  --model gpt-5.2-pro \
  --slug "full-pr-review"
```

---

## Use Cases

### 1. Security Audit

```bash
oracle --prompt "Perform a security audit of this codebase. Look for:
- SQL injection vulnerabilities
- Command injection risks
- Authentication/authorization flaws
- Secrets exposure
- Input validation gaps" \
  --file modal_backend/ \
  --model gpt-5.2-pro \
  --slug "security-audit"
```

### 2. Architecture Review

```bash
oracle --prompt "Review the architecture of this Rafiki:
- Analyze the dual-sandbox pattern (Agent SDK vs CLI)
- Evaluate cold-start mitigation strategies
- Assess scalability of the warm pool approach
- Identify potential bottlenecks" \
  --file modal_backend/main.py \
  --file modal_backend/api/ \
  --file modal_backend/settings/settings.py \
  --model gpt-5.2-pro \
  --slug "architecture-review"
```

### 3. Code Comparison

```bash
oracle --prompt "Compare these two implementations:
- Controller A (port 8001): Agent SDK based
- Controller B (port 8002): CLI subprocess based
What are the tradeoffs? When should each be used?" \
  --file modal_backend/api/controller.py \
  --file modal_backend/api/cli_controller.py \
  --model gpt-5.2-pro
```

### 4. Bug Investigation

```bash
oracle --prompt "The Ralph loop occasionally hangs after iteration 5.
Analyze the loop implementation for:
- Potential deadlocks
- Resource leaks
- Missing timeout handling
- State corruption scenarios" \
  --file modal_backend/ralph/ \
  --file modal_backend/jobs.py \
  --model gpt-5.2-pro \
  --slug "ralph-hang-investigation"
```

### 5. Documentation Generation

```bash
oracle --prompt "Generate API documentation for all HTTP endpoints in this file.
Include: method, path, request body, response schema, error codes." \
  --file modal_backend/main.py \
  --model gpt-5.2-pro \
  --write-output ./API_DOCS.md
```

### 6. Multi-Model Comparison

```bash
oracle --prompt "Review this codebase for performance issues" \
  --file modal_backend/ \
  --models "gpt-5.2-pro,gemini-3-pro" \
  --slug "multi-model-perf-review"
```

---

## Token Management

### Check Token Usage Before Running

```bash
# Dry run with token report
oracle --dry-run --files-report \
  --prompt "Review this code" \
  --file modal_backend/

# Summary only
oracle --dry-run summary \
  --prompt "Review" \
  --file modal_backend/
```

### Token Budget

- Keep total input under **~196k tokens**
- Files larger than **1 MB** are automatically rejected
- Use `--files-report` to see per-file token breakdown

### Reduce Token Usage

```bash
# Exclude large/unnecessary files
oracle --prompt "Review" \
  --file "modal_backend/**/*.py" \
  --file "!**/__pycache__/**" \
  --file "!**/*.pyc"

# Target specific directories
oracle --prompt "Review controllers only" \
  --file modal_backend/api/
```

---

## Session Management

### Named Sessions

```bash
# Use memorable slugs
oracle --prompt "Review authentication flow" \
  --file modal_backend/ \
  --slug "auth-flow-review"
```

### List Recent Sessions

```bash
# Last 24 hours (default)
oracle status

# Custom time window
oracle status --hours 72 --limit 50
```

### Reattach to Session

```bash
# By session ID or slug
oracle session auth-flow-review
oracle session abc123
```

### Avoid Duplicate Runs

```bash
# Oracle blocks duplicate prompts by default
# Force a new run if needed
oracle --prompt "Same prompt" --file modal_backend/ --force
```

---

## Output Options

### Write to File

```bash
oracle --prompt "Generate docs" \
  --file modal_backend/main.py \
  --write-output ./output.md
```

### Copy to Clipboard

```bash
# Render and copy markdown bundle
oracle --render --copy-markdown \
  --prompt "Review code" \
  --file modal_backend/
```

### Multi-Model Output

```bash
# Each model's output goes to separate files
oracle --prompt "Review" \
  --models "gpt-5.2-pro,gemini-3-pro" \
  --write-output ./review.md
# Creates: review.gpt-5.2-pro.md, review.gemini-3-pro.md
```

---

## Tips & Best Practices

1. **Always attach files** - Oracle cannot see your project otherwise
2. **Whole directories beat single files** - More context = better answers
3. **Start with a project briefing** - Stack, services, build steps
4. **Be specific** - Spell out exact requirements and constraints
5. **Use dry-run first** - Check token usage before API calls
6. **Don't re-run on timeout** - Reattach with `oracle session <slug>`
7. **Use slugs** - Makes session management easier
8. **Combine with MCP** - Claude gathers context, Oracle analyzes

---

## Model Options

| Model | Flag | Best For |
|-------|------|----------|
| GPT-5.2 Pro | `--model gpt-5.2-pro` | Deep analysis (default) |
| GPT-5.2 | `--model gpt-5.2` | Faster, lighter tasks |
| GPT-5.2 Instant | `--model gpt-5.2-instant` | Quick queries |
| Gemini 3 Pro | `--model gemini-3-pro` | Alternative perspective |
| Claude 4.5 Sonnet | `--model claude-4.5-sonnet` | Claude via Oracle |

---

## Troubleshooting

### "No API key found"
```bash
export OPENAI_API_KEY="sk-..."
# Or use browser mode
oracle --engine browser ...
```

### Token budget exceeded
```bash
# Check what's using tokens
oracle --dry-run --files-report --prompt "Review" --file modal_backend/

# Reduce scope
oracle --prompt "Review" --file modal_backend/api/
```

### Session timeout
```bash
# Don't re-run! Reattach instead
oracle session <slug-or-id>
```

### Duplicate prompt blocked
```bash
# Force new run
oracle --prompt "Same prompt" --file ... --force

# Or reattach to existing
oracle session
```

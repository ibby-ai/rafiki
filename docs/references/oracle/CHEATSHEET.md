# Oracle CLI Cheatsheet

## Basic Commands

```bash
# Simple query
oracle -p "Your question" -f path/to/file

# Folder
oracle -p "Review" -f modal_backend/

# Multiple folders
oracle -p "Review" -f dir1/ -f dir2/ -f dir3/

# Glob pattern
oracle -p "Review Python" -f "**/*.py"

# Exclude pattern
oracle -p "Review" -f "**/*.py" -f "!**/tests/**"
```

## Git Diff Review

```bash
# Save diff
git diff HEAD~5 -- "*.py" > /tmp/diff.patch

# Review with context
oracle -p "Review this diff" -f /tmp/diff.patch -f modal_backend/

# One-liner (process substitution)
oracle -p "Review diff" -f <(git diff HEAD~5) -f modal_backend/
```

## Token Management

```bash
# Check tokens before running
oracle --dry-run --files-report -p "Review" -f modal_backend/
```

## Session Management

```bash
# List sessions
oracle status

# Reattach
oracle session <id>

# Named session
oracle -p "Review" -f modal_backend/ --slug "my-review"
```

## Output

```bash
# Write to file
oracle -p "Generate docs" -f modal_backend/ --write-output ./output.md

# Copy to clipboard
oracle --render --copy-markdown -p "Review" -f modal_backend/
```

## Models

```bash
# GPT 5.2 Pro (default)
oracle -p "Deep analysis" -f modal_backend/ -m gpt-5.2-pro

# Multi-model
oracle -p "Review" -f modal_backend/ --models "gpt-5.2-pro,gemini-3-pro"

# Browser mode (no API key)
oracle -p "Review" -f modal_backend/ -e browser
```

## MCP + Oracle Pipeline

```bash
# 1. Claude gathers MCP context -> saves to /tmp/mcp_context.md
# 2. Get diff
git diff HEAD~5 > /tmp/diff.patch

# 3. Oracle analyzes everything
oracle -p "Review with MCP context" \
  -f /tmp/mcp_context.md \
  -f /tmp/diff.patch \
  -f modal_backend/ \
  --slug "mcp-review"
```

## Common Flags

| Flag | Short | Description |
|------|-------|-------------|
| `--prompt` | `-p` | Your question |
| `--file` | `-f` | Files/folders/globs |
| `--model` | `-m` | Model selection |
| `--engine` | `-e` | `api` or `browser` |
| `--slug` | `-s` | Session name |
| `--dry-run` | | Preview without API call |
| `--files-report` | | Show token usage |
| `--write-output` | | Save output to file |
| `--force` | | Override duplicate check |

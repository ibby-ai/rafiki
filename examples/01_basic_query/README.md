# Basic Query

The simplest way to run an agent query using the short-lived sandbox pattern.

## How It Works

This example uses `modal run` to spawn an ephemeral sandbox, execute a single query, and terminate. The sandbox is created fresh for each run.

## Usage

```bash
./run.sh
```

Or run individual commands:

```bash
# Simple question
uv run modal run -m modal_backend.main::run_agent_remote --question "What is the capital of France?"

# Coding question
uv run modal run -m modal_backend.main::run_agent_remote --question "Write a Python function that calculates factorial"
```

## What You'll See

The output includes:
- `SystemMessage` - Agent initialization info
- `AssistantMessage` - Agent's response with tool usage
- `ResultMessage` - Final result with timing and cost

## When to Use This Pattern

- One-off queries
- CI/CD pipelines
- Testing and development
- Batch processing (see example 06)

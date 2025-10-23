# Agent Sandbox Starter (Modal + Claude Agent SDK)

A minimal starter to run an agent in an ephemeral Modal Sandbox.

## Requirements

- **Modal CLI**: `pip install modal` and `modal setup`
- **Anthropic API key**: store in a Modal Secret named `anthropic-secret` with key `ANTHROPIC_API_KEY`

## Quickstart

- **Run locally (spawns a Sandbox and executes `runner.py`)**

```bash
modal run main.py
```

- **Ask a custom question via the controller**

```bash
modal run main.py::sandbox_controller --question "What is the capital of France?"
```

- **Run the agent as a remote function**

```bash
modal run main.py::run_agent_remote --question "Explain REST vs gRPC"
```

- **Keep a dev deployment running (hot-reload style)**

```bash
modal serve main.py
```

- **Deploy to production**

```bash
modal deploy main.py
```

### How it works

- `main.py`: Defines a Modal `App` and functions to run the agent inside a Sandbox or remotely.
- `runner.py`: Uses `claude_agent_sdk` to run an interactive query and stream responses.
- `utils/env_templates.py`: Builds the Modal image, sets workdir, and attaches required secrets.
- `utils/tools.py`: Example MCP tools (calculate, translate, search) and allowed tool list. Obtained from [Claude Agent SDK Documentation](https://docs.claude.com/en/api/agent-sdk/python)
- `utils/prompts.py`: System prompt and default question.

### Configure & extend

- **Change the system prompt**: edit `utils/prompts.py`.
- **Add or modify tools**: edit `utils/tools.py` (update tool list and allowed tools as needed).
- **Adjust runtime image/secrets**: edit `utils/env_templates.py`.

### Troubleshooting

- Ensure the Modal secret `anthropic-secret` exists and contains `ANTHROPIC_API_KEY`.
- Run `modal setup` if you haven’t logged in or configured Modal locally.
- Use `modal serve main.py` during development to iterate quickly.

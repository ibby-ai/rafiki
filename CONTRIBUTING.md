# Contributing to Rafiki

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

## Getting Started

### Prerequisites

1. **Python 3.11+** with [uv](https://docs.astral.sh/uv/getting-started/installation/) package manager
2. **Modal account** configured (`modal setup`)
3. **OpenAI API key** stored in Modal secrets

### Development Setup

```bash
# Clone the repository
git clone https://github.com/Saidiibrahim/rafiki.git
cd rafiki

# Create and activate virtual environment
uv venv
source .venv/bin/activate

# Install dependencies
uv sync

# Set up Modal secret (one-time)
modal secret create openai-secret OPENAI_API_KEY=your-key-here
```

### Running the Project

```bash
# Run agent locally
modal run -m modal_backend.main

# Start dev server with hot reload
modal serve -m modal_backend.main

# Run tests
uv run pytest
```

## Finding Things to Work On

### Good First Issues

Look for issues labeled [`good first issue`](https://github.com/Saidiibrahim/rafiki/labels/good%20first%20issue) - these are beginner-friendly tasks.

### Help Wanted

Issues labeled [`help wanted`](https://github.com/Saidiibrahim/rafiki/labels/help%20wanted) are areas where contributions are especially welcome.

### Roadmap

Check our [ROADMAP.md](./ROADMAP.md) to see planned features and where you can contribute.

### GitHub Project Board

Our [project board](https://github.com/Saidiibrahim/rafiki/projects) shows the current status of all work items.

## Making Contributions

### Code Style

- We use **ruff** for linting and formatting
- Pre-commit hooks run automatically on commit
- Run manually with: `uv run pre-commit run --all-files`

### Pull Request Process

1. **Fork** the repository
2. **Create a branch** for your feature: `git checkout -b feature/your-feature`
3. **Make your changes** following the code style guidelines
4. **Test your changes**: `uv run pytest`
5. **Commit** with a descriptive message
6. **Push** and create a Pull Request

### Commit Messages

Use clear, descriptive commit messages:
- `feat: add new MCP tool for X`
- `fix: resolve issue with sandbox timeout`
- `docs: update API usage examples`
- `refactor: simplify agent loop logic`

### Testing

- Add tests for new functionality in `tests/`
- Ensure existing tests pass: `uv run pytest`
- Test structure mirrors `modal_backend/` package structure

## Areas of Contribution

### Adding New Tools

See `modal_backend/mcp_tools/` for examples. New tools should:
1. Be created in a separate file
2. Be registered in `registry.py`
3. Include tests

### Improving Documentation

Documentation lives in:
- `docs/` - Detailed guides
- `README.md` - Quick start
- `AGENTS.md` - AI agent instructions

### Observability & Tracing

We're actively working on adding tracing capabilities. See [ROADMAP.md](./ROADMAP.md) for details.

## Questions?

- Start a [Discussion](https://github.com/Saidiibrahim/rafiki/discussions) for questions
- Check existing issues before creating new ones
- Join the conversation in Ideas discussions

## Code of Conduct

Be respectful and constructive in all interactions. We're building something together!

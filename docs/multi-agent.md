# Multi-Agent Architecture

This guide covers the multi-agent architecture for running specialized AI agents with different capabilities, tools, and orchestration patterns.

## Table of Contents

- [Overview](#overview)
- [Core Concepts](#core-concepts)
  - [AgentConfig](#agentconfig)
  - [AgentRegistry](#agentregistry)
  - [AgentExecutor](#agentexecutor)
- [Built-in Agent Types](#built-in-agent-types)
- [Dual Orchestration](#dual-orchestration)
  - [SDK Native Subagents (Task Tool)](#sdk-native-subagents-task-tool)
  - [Job-Based Spawning (spawn_session)](#job-based-spawning-spawn_session)
  - [Comparison](#comparison)
- [Creating Custom Agents](#creating-custom-agents)
- [Creating Subagents](#creating-subagents)
- [API Usage](#api-usage)
  - [CLI Usage](#cli-usage)
  - [HTTP API Usage](#http-api-usage)
- [Best Practices](#best-practices)

## Overview

The multi-agent architecture allows you to define specialized agents with:

- **Custom system prompts**: Tailored behavior and personality
- **Specific tool access**: Limited or expanded capabilities based on role
- **Orchestration support**: Delegation to subagents for complex tasks
- **Turn limits**: Fine-grained control over agent execution

### When to Use Multi-Agent

| Scenario | Recommended Approach |
|----------|---------------------|
| Simple queries, general tasks | `default` agent |
| Marketing content, copywriting | `marketing` agent |
| Research requiring multiple sources | `research` agent |
| Custom domain-specific tasks | Create a custom agent type |
| Complex workflows with parallel work | Orchestration with subagents |

## Core Concepts

### AgentConfig

`AgentConfig` is the central dataclass that defines agent behavior:

```python
from agent_sandbox.agents.base import AgentConfig

config = AgentConfig(
    name="my-agent",                    # Unique identifier
    display_name="My Agent",            # Human-readable name
    description="What this agent does", # Used for documentation
    system_prompt="...",                # Behavior-shaping prompt
    allowed_tools=["Read", "Write"],    # Tool whitelist (empty = defaults)
    max_turns=30,                       # Turn limit (None = global setting)
    mcp_servers=None,                   # Custom MCP servers (None = defaults)
    can_spawn_subagents=False,          # Enable job-based orchestration
    subagent_types=["default"],         # Spawnable agent types
    subagents=None,                     # SDK native subagent definitions
)
```

**Key Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Unique identifier used in CLI and API |
| `display_name` | `str` | Human-readable name for UI/logging |
| `description` | `str` | Documentation of agent purpose |
| `system_prompt` | `str` | Shapes agent behavior and personality |
| `allowed_tools` | `list[str]` | Tool whitelist; empty list uses defaults |
| `max_turns` | `int \| None` | Override global turn limit |
| `mcp_servers` | `dict \| None` | Custom MCP servers; None uses defaults |
| `can_spawn_subagents` | `bool` | Enable spawn_session tools |
| `subagent_types` | `list[str]` | Which agent types can be spawned |
| `subagents` | `dict[str, AgentDefinition] \| None` | SDK native subagents |

### AgentRegistry

The `AgentRegistry` is a singleton that manages agent configurations:

```python
from agent_sandbox.agents.registry import (
    get_agent_config,
    get_agent_executor,
    list_agent_types,
    register_agent,
)

# List available agent types
print(list_agent_types())  # ["default", "marketing", "research"]

# Get configuration for an agent type
config = get_agent_config("marketing")
print(config.system_prompt)

# Get an executor for direct use
executor = get_agent_executor("research")
async for msg in executor.execute("Research AI trends"):
    print(msg)

# Register a custom agent type
from agent_sandbox.agents.base import AgentConfig
register_agent(AgentConfig(name="custom", ...))
```

### AgentExecutor

`AgentExecutor` is an abstract base class for agent execution, with `ClaudeAgentExecutor` as the default implementation:

```python
from agent_sandbox.agents.base import AgentExecutor, ClaudeAgentExecutor

# Create executor from config
executor = ClaudeAgentExecutor(config)

# Execute a query
async for msg in executor.execute("Your question here"):
    print(msg)
```

The executor handles:
- Building `ClaudeAgentOptions` from the config
- Managing the Claude SDK client lifecycle
- Streaming response messages
- Injecting execution context (job info, workspace paths)

## Built-in Agent Types

| Agent | Purpose | Tools | Subagents |
|-------|---------|-------|-----------|
| `default` | General-purpose coding | All default tools | Can spawn any type |
| `marketing` | Content and copywriting | Read, Write, WebSearch, WebFetch | None (leaf worker) |
| `research` | Multi-agent research | Task, Read, Write, Glob, WebSearch, spawn_session | researcher, data-analyst, report-writer |

### Default Agent

General-purpose agent that maintains backward compatibility:

```bash
modal run -m agent_sandbox.app::run_agent_remote --question "Explain Python decorators"
```

**Capabilities:**
- Access to all standard tools
- Can spawn child agents of any type
- Uses the original `SYSTEM_PROMPT`

### Marketing Agent

Specialized for marketing content creation:

```bash
modal run -m agent_sandbox.app::run_agent_remote \
  --question "Write a tagline for a productivity app" \
  --agent-type marketing
```

**Capabilities:**
- Web search for market research
- File operations for content creation
- Limited tool access for focused work
- 30-turn maximum

### Research Agent

Multi-agent research coordinator with dual orchestration:

```bash
modal run -m agent_sandbox.app::run_agent_remote \
  --question "Research the current state of AI agents" \
  --agent-type research
```

**Capabilities:**
- SDK native subagents: researcher, data-analyst, report-writer
- Job-based spawning for parallel investigation
- 50-turn maximum for complex research
- Comprehensive web and file access

## Dual Orchestration

The architecture supports two complementary orchestration mechanisms:

### SDK Native Subagents (Task Tool)

In-process delegation using the built-in Task tool and `AgentDefinition`:

```python
from claude_agent_sdk import AgentDefinition

subagents = {
    "researcher": AgentDefinition(
        description="Gathers information from web sources",
        tools=["WebSearch", "WebFetch", "Write"],
        prompt="You are a research specialist...",
        model="haiku",
    ),
}
```

**How it works:**
1. Lead agent recognizes it needs research
2. Uses Task tool: `Task(agent="researcher", prompt="Find information about X")`
3. Subagent executes in-process using the same SDK client
4. Results return immediately to lead agent

**Best for:**
- Sequential workflows
- Low-latency delegation
- Tight integration between agents
- Simple orchestration patterns

### Job-Based Spawning (spawn_session)

Parallel execution via isolated sandbox jobs:

```python
# Agent uses MCP tools:
# mcp__sessions__spawn_session
# mcp__sessions__check_session_status
# mcp__sessions__get_session_result
# mcp__sessions__list_child_sessions
```

**How it works:**
1. Lead agent spawns child sessions via `spawn_session` tool
2. Children run in isolated sandboxes
3. Lead agent monitors via `check_session_status`
4. Results collected via `get_session_result`

**Best for:**
- Parallel investigation
- Long-running tasks
- Fault isolation
- Resource-intensive operations

### Comparison

| Feature | SDK Native (Task) | Job-Based (spawn_session) |
|---------|-------------------|---------------------------|
| Execution | In-process | Isolated sandbox |
| Latency | Low | Higher (sandbox startup) |
| Parallelism | Sequential | Truly parallel |
| Fault isolation | Shared context | Fully isolated |
| Resource usage | Shared | Independent |
| State sharing | Direct | Via files/API |
| Turn counting | Shared budget | Independent budgets |

## Creating Custom Agents

### Step 1: Create Agent Configuration

Create a new file in `agent_sandbox/agents/types/`:

```python
# agent_sandbox/agents/types/customer_support.py
"""Customer support agent configuration."""

from agent_sandbox.agents.base import AgentConfig

SUPPORT_PROMPT = """You are a customer support specialist.

Your role:
- Answer customer questions clearly and empathetically
- Look up relevant documentation
- Escalate complex issues appropriately

Always maintain a helpful, professional tone.
"""

def customer_support_config() -> AgentConfig:
    """Create the customer support agent configuration."""
    return AgentConfig(
        name="customer-support",
        display_name="Customer Support Agent",
        description="Handles customer inquiries with empathy and precision.",
        system_prompt=SUPPORT_PROMPT,
        allowed_tools=[
            "Read",                     # Read knowledge base
            "Glob",                     # Search for docs
            "WebFetch(*)",              # Fetch external resources
            "mcp__utilities__calculate", # Basic calculations
        ],
        max_turns=20,
        can_spawn_subagents=False,  # Leaf worker
    )
```

### Step 2: Register the Agent

Add to `agent_sandbox/agents/registry.py`:

```python
def _initialize_defaults(self) -> None:
    """Initialize default agent types."""
    from agent_sandbox.agents.types.default import default_agent_config
    from agent_sandbox.agents.types.marketing import marketing_agent_config
    from agent_sandbox.agents.types.research import research_agent_config
    from agent_sandbox.agents.types.customer_support import customer_support_config

    self.register(default_agent_config())
    self.register(marketing_agent_config())
    self.register(research_agent_config())
    self.register(customer_support_config())  # Add this line
```

### Step 3: Use the Agent

```bash
# CLI
modal run -m agent_sandbox.app::run_agent_remote \
  --question "How do I reset my password?" \
  --agent-type customer-support

# HTTP API
curl -X POST 'https://your-url.modal.run/query' \
  -H 'Content-Type: application/json' \
  -d '{"question":"How do I reset my password?","agent_type":"customer-support"}'
```

## Creating Subagents

### SDK Native Subagents

For in-process delegation, add `AgentDefinition` objects to your config:

```python
from claude_agent_sdk import AgentDefinition
from agent_sandbox.agents.base import AgentConfig

def coordinator_config() -> AgentConfig:
    """Coordinator with multiple subagents."""

    subagents = {
        "planner": AgentDefinition(
            description="Creates detailed project plans from requirements.",
            tools=["Read", "Write"],
            prompt="You are a project planning specialist...",
            model="sonnet",  # Use more capable model for planning
        ),
        "executor": AgentDefinition(
            description="Implements tasks from the plan.",
            tools=["Read", "Write", "Bash"],
            prompt="You are a task executor...",
            model="haiku",  # Use faster model for execution
        ),
        "reviewer": AgentDefinition(
            description="Reviews completed work for quality.",
            tools=["Read", "Glob"],
            prompt="You are a code reviewer...",
            model="haiku",
        ),
    }

    return AgentConfig(
        name="coordinator",
        display_name="Project Coordinator",
        description="Coordinates planning, execution, and review.",
        system_prompt="You coordinate complex projects...",
        allowed_tools=[
            "Task",      # Enable subagent delegation
            "Read",
            "Write",
            "Glob",
        ],
        subagents=subagents,
    )
```

**Usage in the lead agent's conversation:**

```
User: Create a login page for my app

Agent: I'll coordinate this project using my team:
1. First, let me have the planner create a spec...
   [Uses Task tool with agent="planner"]
2. Now the executor will implement it...
   [Uses Task tool with agent="executor"]
3. Finally, the reviewer will check the work...
   [Uses Task tool with agent="reviewer"]
```

### Job-Based Subagent Spawning

For parallel or isolated execution, enable spawn_session tools:

```python
return AgentConfig(
    name="parallel-coordinator",
    # ...
    allowed_tools=[
        "Read",
        "Write",
        "mcp__sessions__spawn_session",
        "mcp__sessions__check_session_status",
        "mcp__sessions__get_session_result",
        "mcp__sessions__list_child_sessions",
    ],
    can_spawn_subagents=True,
    subagent_types=["default", "marketing", "research"],
)
```

## API Usage

### CLI Usage

```bash
# Default agent
modal run -m agent_sandbox.app::run_agent_remote --question "Your question"

# Specify agent type
modal run -m agent_sandbox.app::run_agent_remote \
  --question "Write a tagline" \
  --agent-type marketing

# With session resumption
modal run -m agent_sandbox.app::run_agent_remote \
  --question "Continue the research" \
  --agent-type research \
  --session-id "sess_abc123"

# Direct loop execution (for testing)
python -m agent_sandbox.agents.loop --question "Test" --agent-type default
```

### HTTP API Usage

**Basic query with agent type:**

```bash
curl -X POST 'https://your-url.modal.run/query' \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "Write a product description",
    "agent_type": "marketing"
  }'
```

**Streaming with agent type:**

```bash
curl -N -X POST 'https://your-url.modal.run/query_stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "Research cloud computing trends",
    "agent_type": "research"
  }'
```

**Submit background job with agent type:**

```bash
curl -X POST 'https://your-url.modal.run/submit' \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "Write a comprehensive market analysis",
    "agent_type": "marketing",
    "tenant_id": "acme",
    "user_id": "user-123"
  }'
```

## Best Practices

### Agent Design

1. **Single Responsibility**: Each agent should have a focused purpose
2. **Clear Boundaries**: Define explicit tool access based on role
3. **Appropriate Turn Limits**: Set limits based on task complexity
4. **Meaningful Prompts**: Write detailed system prompts that guide behavior

### Tool Configuration

1. **Principle of Least Privilege**: Only grant tools the agent needs
2. **Use Wildcards Carefully**: `WebSearch(*)` grants broad access
3. **Consider Security**: Limit file system access for public-facing agents
4. **Test Tool Interactions**: Verify tools work together as expected

### Orchestration Patterns

1. **Choose the Right Mechanism**:
   - Use SDK native subagents for sequential, low-latency work
   - Use job spawning for parallel or resource-intensive tasks

2. **Design for Failure**:
   - Job-based spawning provides isolation
   - SDK native subagents share failure modes with parent

3. **Mind the Turn Budget**:
   - SDK native subagents share the parent's turn budget
   - Job-based children have independent budgets

4. **File-Based Communication**:
   - Use `/data/` for persistent artifacts
   - Establish conventions for subagent outputs (e.g., `/data/research_notes/`)

### Performance

1. **Use Haiku for Subagents**: Faster and cheaper for focused tasks
2. **Limit Parallel Spawns**: Too many jobs can overwhelm resources
3. **Cache Research**: Store findings in `/data/` for reuse
4. **Set Reasonable Timeouts**: Job-based spawning has startup overhead

## Related Documentation

- [Architecture Overview](./architecture.md) - System architecture
- [API Usage Guide](./api-usage.md) - HTTP endpoint documentation
- [Tool Development Guide](./tool-development.md) - Creating custom tools
- [Configuration Guide](./configuration.md) - Settings and environment

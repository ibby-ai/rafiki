# Batch Processing

Demonstrates processing multiple queries sequentially.

## Use Cases

- Generating code for multiple files
- Answering a series of questions
- Data transformation pipelines
- CI/CD automated checks

## Usage

```bash
./run.sh
```

## How It Works

The script loops through an array of questions, running each through the agent:

```bash
QUESTIONS=(
    "Question 1"
    "Question 2"
)

for q in "${QUESTIONS[@]}"; do
    modal run -m agent_sandbox.app::run_agent_remote --question "$q"
done
```

## Performance Considerations

- Each query spawns a new Modal function (cold start ~2-5s)
- For high-throughput, consider the HTTP endpoint pattern
- Queries run sequentially; parallel execution requires separate terminals

## Customization

Edit `run.sh` to add your own questions:

```bash
QUESTIONS=(
    "Your question here"
    "Another question"
)
```

"""Python client example for agent-sandbox-starter API.

Usage:
    python client.py https://your-org--test-sandbox-http-app-dev.modal.run
"""

import json
import sys

import httpx


def health_check(base_url: str) -> dict:
    """Check if the service is healthy."""
    with httpx.Client(timeout=10.0) as client:
        response = client.get(f"{base_url}/health")
        response.raise_for_status()
        return response.json()


def query_agent(base_url: str, question: str) -> dict:
    """Send a query to the agent and return the response."""
    with httpx.Client(timeout=120.0) as client:
        response = client.post(f"{base_url}/query", json={"question": question})
        response.raise_for_status()
        return response.json()


def stream_query(base_url: str, question: str):
    """Stream a query response using SSE."""
    with httpx.Client(timeout=None) as client:
        with client.stream(
            "POST", f"{base_url}/query_stream", json={"question": question}
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                        event_type = data.get("type", "unknown")
                        print(f"[{event_type}] ", end="")
                        if event_type == "result":
                            print(data.get("result", "")[:100])
                        else:
                            print(str(data)[:100])
                    except json.JSONDecodeError:
                        print(f"Raw: {line}")


def get_service_info(base_url: str) -> dict:
    """Get information about the background sandbox service."""
    with httpx.Client(timeout=30.0) as client:
        response = client.get(f"{base_url}/service_info")
        response.raise_for_status()
        return response.json()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python client.py <base_url>")
        print("Example: python client.py https://your-org--test-sandbox-http-app-dev.modal.run")
        sys.exit(1)

    base_url = sys.argv[1].rstrip("/")

    print("=== Python Client Example ===\n")

    print("1. Health check:")
    print(f"   {health_check(base_url)}\n")

    print("2. Service info:")
    info = get_service_info(base_url)
    print(f"   Sandbox ID: {info.get('sandbox_id', 'N/A')}")
    print(f"   URL: {info.get('url', 'N/A')}\n")

    print("3. Non-streaming query:")
    result = query_agent(base_url, "What is the capital of Japan?")
    summary = result.get("summary", {}).get("text", "No summary")
    print(f"   {summary[:200]}\n")

    print("4. Streaming query (first few events):")
    print("   ", end="")
    stream_query(base_url, "Say hello")

    print("\n\n=== Done ===")

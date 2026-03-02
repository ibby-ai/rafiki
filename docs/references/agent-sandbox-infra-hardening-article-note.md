# Article Reference: Agent Sandbox Infrastructure Hardening

## Source Metadata
- Title: `How We Built Secure, Scalable Agent Sandbox Infrastructure`
- Author: Larsen Cundric (`@larsencc`)
- Source URL: `https://x.com/larsencc/status/2027225210412470668`
- Retrieved: 2026-03-02 (Australia/Adelaide) via `agent-browser`
- Retrieval command: `agent-browser open https://x.com/larsencc/status/2027225210412470668 && agent-browser snapshot -c`

## Distilled Principles Used in Rafiki Plan
1. Control plane should own durable authority and credentials.
2. Sandbox runtime should receive minimal secrets and be treated as disposable.
3. High-risk execution paths must fail closed with explicit policy rails.
4. Runtime hardening should happen before arbitrary code/tool execution.
5. Artifact transfer should use scoped, time-bounded signed access.
6. Scaling boundaries should be independently testable (edge control plane vs runtime).

## Rafiki Mapping Summary
| Principle | Rafiki Mapping | Status |
| --- | --- | --- |
| Control-plane authority | Cloudflare Worker + SessionAgent DO route and authorize public traffic | `already/partial` |
| Minimal sandbox credentials | Modal sandbox secret injection currently broader than target | `gap` |
| Runtime hardening | Internal auth exists, but non-root/env scrub posture is incomplete | `gap` |
| Safe tool execution | Existing Bash/WebFetch checks exist; calculate path still used `eval` | `gap` |
| Budget rails | Rate limiting exists at edge, but deterministic per-session budget denial flow is incomplete | `partial` |
| Artifact transfer hardening | Artifact endpoints exist; scoped signed transfer and abuse-case coverage are incomplete | `gap` |

## Why This Internal Note Exists
This note makes the source durable for future execution and audit work so implementation decisions do not depend on live social-media availability.

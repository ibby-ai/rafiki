#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/oracle/code_quality_governance_review.sh [--dry-run|--real-run] [summary|json|full]

Defaults:
  --dry-run summary

Behavior:
  - Builds an Oracle review bundle under /tmp/oracle-review/code-quality-governance
  - Sources OPENAI_API_KEY from ./.env when available
  - Forces workspace-local Oracle state via ORACLE_HOME_DIR=.oracle
  - Uses the API engine and gpt-5.2-pro by default
  - Refuses to run a real submission unless --real-run is provided explicitly
EOF
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

RUN_MODE="--dry-run"
DRY_RUN_MODE="summary"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == "--dry-run" || "${1:-}" == "--real-run" ]]; then
  RUN_MODE="$1"
  shift
fi

if [[ $# -gt 0 ]]; then
  case "$1" in
    summary|json|full)
      DRY_RUN_MODE="$1"
      shift
      ;;
    *)
      echo "Unknown dry-run mode: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
fi

if [[ $# -gt 0 ]]; then
  echo "Unexpected arguments: $*" >&2
  usage >&2
  exit 1
fi

export ORACLE_HOME_DIR="${ORACLE_HOME_DIR:-.oracle}"

if [[ -f ./.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source ./.env
  set +a
fi

: "${OPENAI_API_KEY:?OPENAI_API_KEY must be set via environment or ./.env}"

BUNDLE_DIR="${BUNDLE_DIR:-/tmp/oracle-review/code-quality-governance}"
mkdir -p "$BUNDLE_DIR"

PROMPT_FILE="$BUNDLE_DIR/oracle-prompt.txt"
TREE_FILE="$BUNDLE_DIR/project-tree.txt"
STATUS_FILE="$BUNDLE_DIR/git-status.txt"
STAT_FILE="$BUNDLE_DIR/diff-stat.txt"
TRACKED_DIFF_FILE="$BUNDLE_DIR/tracked.diff"
ATTACHMENTS_FILE="$BUNDLE_DIR/attachments.txt"
STATUS_SNAPSHOT_FILE="$BUNDLE_DIR/oracle-status.txt"

git status --short > "$STATUS_FILE"
git diff --stat > "$STAT_FILE"
git diff -- . ':(exclude)edge-control-plane/package-lock.json' ':(exclude)uv.lock' > "$TRACKED_DIFF_FILE"
oracle status --hours 24 --limit 10 > "$STATUS_SNAPSHOT_FILE" || true

python3 - <<'PY' > "$TREE_FILE"
from pathlib import Path

roots = [
    Path(".github"),
    Path(".claude/agents"),
    Path("docs/exec-plans/completed/code-quality-governance"),
    Path("docs/generated"),
    Path("docs/product-specs"),
    Path("docs/references"),
    Path("edge-control-plane/src/auth"),
    Path("edge-control-plane/src/contracts"),
    Path("edge-control-plane/src/routes"),
    Path("edge-control-plane/tests/contracts"),
    Path("edge-control-plane/tests/integration"),
    Path("modal_backend/api"),
    Path("modal_backend/models"),
    Path("modal_backend/platform_services"),
    Path("modal_backend/security"),
    Path("scripts/oracle"),
    Path("scripts/quality"),
    Path("tests"),
]

max_depth = 2
skip = {".git", ".venv", ".oracle", "node_modules", "__pycache__"}

def walk(path: Path, prefix: str = "", depth: int = 0) -> None:
    if depth > max_depth:
        return
    entries = sorted(
        [p for p in path.iterdir() if p.name not in skip],
        key=lambda p: (not p.is_dir(), p.name.lower()),
    )
    for index, entry in enumerate(entries):
        connector = "└── " if index == len(entries) - 1 else "├── "
        print(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")
        if entry.is_dir() and depth < max_depth:
            extension = "    " if index == len(entries) - 1 else "│   "
            walk(entry, prefix + extension, depth + 1)

print("Relevant review tree")
for root in roots:
    if not root.exists():
        continue
    print(f"\n{root}/")
    walk(root, depth=0)
PY

cat > "$PROMPT_FILE" <<'EOF'
You are reviewing the Rafiki code-quality-governance rollout in a mixed Python plus Cloudflare TypeScript repository.

Repository context:
- Python runtime and APIs live under `modal_backend/**`.
- The public control plane lives under `edge-control-plane/**`.
- This wave intentionally makes only scoped leaf modules blocking; major orchestration hubs remain advisory.
- The public contract surface is the Cloudflare Worker API documented in `docs/references/api-usage.md`.

Rollout summary:
- Added a canonical governance contract, waiver registry, active ExecPlan, proof artifact, and a mandatory `boundary-enforcer` reviewer role.
- Added scoped Python enforcement with Ruff doc rules, targeted mypy, Import Linter, and suppression/waiver validation.
- Added Worker enforcement with Zod runtime schemas, TypeDoc validation, dependency-cruiser boundaries, and focused contract/integration tests.
- Split CI into Python validation, Worker validation, and an always-run proof job.

Validation state:
- Governance-specific checks are passing.
- Root `pytest` still fails in pre-existing unrelated controller-rollout and sandbox-auth suites; see the attached proof artifact for exact classification.

What I want from you:
1. Find any remaining architectural boundary violations, transport/runtime validation gaps, or cohesion regressions.
2. Find any incorrect, incomplete, or misleading public API documentation or audit-trail handling.
3. Find any ways the CI, proof, waiver, or reviewer workflow could still be bypassed or become stale.
4. Focus on concrete, high-signal findings with file references. This is not a style pass.

Review constraints:
- Treat this as an enforceable engineering contract, not a cleanup task.
- Prioritize findings by severity.
- If no material issues remain, say so explicitly and list only residual non-blocking risks.
EOF

ATTACHMENTS=(
  "!**/__pycache__/**"
  "!**/*.pyc"
  "$TREE_FILE"
  "$STATUS_FILE"
  "$STAT_FILE"
  "$TRACKED_DIFF_FILE"
  "$STATUS_SNAPSHOT_FILE"
  ".github/workflows/ci.yml"
  ".claude/agents/boundary-enforcer.md"
  ".importlinter"
  "ARCHITECTURE.md"
  "Makefile"
  "mypy-governance.ini"
  "pyproject.toml"
  "scripts/oracle/code_quality_governance_review.sh"
  "scripts/quality/**/*.py"
  "docs/AGENT_COLLABORATION_PROCESS.md"
  "docs/QUALITY_SCORE.md"
  "docs/RELIABILITY.md"
  "docs/SECURITY.md"
  "docs/product-specs/code-quality-governance.md"
  "docs/references/code-quality-governance.md"
  "docs/references/code-quality-waivers.json"
  "docs/references/code-quality-waivers.schema.json"
  "docs/references/api-usage.md"
  "docs/exec-plans/completed/code-quality-governance/**/*.md"
  "docs/generated/code-quality-governance-proof-2026-03-13T11-59-01+1030.json"
  "edge-control-plane/package.json"
  "edge-control-plane/dependency-cruiser.cjs"
  "edge-control-plane/typedoc.contracts.json"
  "edge-control-plane/src/auth/**/*.ts"
  "edge-control-plane/src/contracts/**/*.ts"
  "edge-control-plane/src/routes/jobs-proxy.ts"
  "edge-control-plane/src/routes/schedules-proxy.ts"
  "edge-control-plane/src/routes/session-stop-proxy.ts"
  "edge-control-plane/src/index.ts"
  "edge-control-plane/src/durable-objects/session-agent.ts"
  "edge-control-plane/src/types.ts"
  "edge-control-plane/tests/contracts/**/*.ts"
  "edge-control-plane/tests/integration/jobs-proxy.integration.test.ts"
  "edge-control-plane/tests/integration/worker-governance.integration.test.ts"
  "modal_backend/api/serialization.py"
  "modal_backend/platform_services/webhooks.py"
  "modal_backend/security/**/*.py"
  "modal_backend/models/sandbox.py"
  "modal_backend/models/session_spawn.py"
  "modal_backend/models/jobs.py"
  "tests/test_code_quality_waivers.py"
)

printf '%s\n' "${ATTACHMENTS[@]}" > "$ATTACHMENTS_FILE"

ORACLE_CMD=(
  oracle
  --engine api
  --model gpt-5.2-pro
  --slug "code-quality-governance-review"
  --files-report
  --prompt "$(cat "$PROMPT_FILE")"
)

for attachment in "${ATTACHMENTS[@]}"; do
  ORACLE_CMD+=(--file "$attachment")
done

if [[ "$RUN_MODE" == "--dry-run" ]]; then
  ORACLE_CMD+=(--dry-run "$DRY_RUN_MODE")
else
  echo "REAL ORACLE RUN REQUESTED" >&2
fi

printf 'Oracle bundle written to %s\n' "$BUNDLE_DIR"
printf 'Prompt file: %s\n' "$PROMPT_FILE"
printf 'Attachments file: %s\n' "$ATTACHMENTS_FILE"
printf 'Command:\n'
printf '  %q' "${ORACLE_CMD[@]}"
printf '\n'

"${ORACLE_CMD[@]}"

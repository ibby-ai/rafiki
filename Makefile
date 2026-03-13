# Makefile helpers for serving the Modal app and testing with curl.
#
# Usage:
#   Terminal 1: make serve
#   Terminal 2 (question input): make curl Q='What is 2+2?'
#
# Set this once to your dev endpoint URL (from `modal serve -m modal_backend.main`).
# Example: https://your-org--modal-backend-http-app-dev.modal.run
DEV_URL ?=
MODAL_PROXY_KEY ?=
MODAL_PROXY_SECRET ?=
CURL_PROXY_HEADERS = $(if $(and $(MODAL_PROXY_KEY),$(MODAL_PROXY_SECRET)),-H Modal-Key:$(MODAL_PROXY_KEY) -H Modal-Secret:$(MODAL_PROXY_SECRET),)

.PHONY: serve dev-url curl curl-q ask stream health info terminate snapshot tail-logs run deploy lint typecheck test format governance-python governance-worker governance-docs governance governance-proof release-quality quality-contracts

serve:
	@echo "Serving Modal app..."; \
	uv run modal serve -m modal_backend.main

run:
	@echo "Running agent locally..."; \
	uv run modal run -m modal_backend.main

deploy:
	@echo "Deploying to production..."; \
	uv run modal deploy -m modal_backend.deploy

dev-url:
	@test -n "$(DEV_URL)" || { echo "Missing DEV_URL. Export the Modal dev URL first."; exit 1; } ; \
	echo $(DEV_URL)

# Ask a question (POST /query with JSON object)
curl:
	@test -n "$(DEV_URL)" || { echo "Missing DEV_URL. Export the Modal dev URL or pass DEV_URL=..."; exit 1; } ; \
	@test -n "$(Q)" || { echo "Missing Q. Usage: make curl Q='Your question'"; exit 1; } ; \
	curl -X POST '$(DEV_URL)/query' \
	  -H 'Content-Type: application/json' $(CURL_PROXY_HEADERS) \
	  -d '{"question":"$(Q)"}'

# Stream a response (POST /query_stream)
stream:
	@test -n "$(DEV_URL)" || { echo "Missing DEV_URL. Export the Modal dev URL or pass DEV_URL=..."; exit 1; } ; \
	@test -n "$(Q)" || { echo "Missing Q. Usage: make stream Q='Your question'"; exit 1; } ; \
	curl -N -X POST '$(DEV_URL)/query_stream' \
	  -H 'Content-Type: application/json' $(CURL_PROXY_HEADERS) \
	  -d '{"question":"$(Q)"}'

health:
	@test -n "$(DEV_URL)" || { echo "Missing DEV_URL. Export the Modal dev URL or pass DEV_URL=..."; exit 1; } ; \
	curl -sS $(CURL_PROXY_HEADERS) '$(DEV_URL)/health'

info:
	@test -n "$(DEV_URL)" || { echo "Missing DEV_URL. Export the Modal dev URL or pass DEV_URL=..."; exit 1; } ; \
	curl -sS $(CURL_PROXY_HEADERS) '$(DEV_URL)/service_info'

terminate:
	uv run modal run -m modal_backend.main::terminate_service_sandbox

snapshot:
	uv run modal run -m modal_backend.main::snapshot_service

tail-logs:
	uv run modal run -m modal_backend.main::tail_logs

# Development and testing
lint:
	@echo "Running linter..."; \
	uv run ruff check modal_backend/ tests/

typecheck:
	@echo "Running type checker..."; \
	uv run mypy modal_backend/ tests/

test:
	@echo "Running tests..."; \
	uv run python -m pytest tests/ -v

format:
	@echo "Formatting code..."; \
	uv run ruff format modal_backend/ tests/

governance-python:
	uv run python scripts/quality/check_python_governance.py

governance-worker:
	npm --prefix edge-control-plane run check:contracts
	npm --prefix edge-control-plane run docs:api
	npm --prefix edge-control-plane run check:boundaries

governance-docs:
	uv run python scripts/quality/check_docs_governance.py

governance: governance-python governance-worker governance-docs

release-quality:
	uv run ruff check .
	uv run pytest
	npm --prefix edge-control-plane run check
	cd edge-control-plane && ./node_modules/.bin/tsc --noEmit
	npm --prefix edge-control-plane run test:integration

governance-proof:
	uv run python scripts/quality/write_code_quality_proof.py

quality-contracts: release-quality governance

# Back-compat aliases
ask: curl
curl-q: curl

# Prevent Make from trying to execute stray words as targets
%:
	@:

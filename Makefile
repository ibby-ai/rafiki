# Makefile helpers for serving the Modal app and testing with curl.
#
# Usage:
#   Terminal 1: make serve
#   Terminal 2 (question input): make curl Q='What is 2+2?'
#
# Set this once to your dev endpoint URL (from `modal serve -m modal_backend.main`).
# Example: https://your-org--test-sandbox-http-app-dev.modal.run
DEV_URL ?= https://saidiibrahim--test-sandbox-http-app-dev.modal.run
MODAL_PROXY_KEY ?=
MODAL_PROXY_SECRET ?=
CURL_PROXY_HEADERS = $(if $(and $(MODAL_PROXY_KEY),$(MODAL_PROXY_SECRET)),-H Modal-Key:$(MODAL_PROXY_KEY) -H Modal-Secret:$(MODAL_PROXY_SECRET),)

.PHONY: serve dev-url curl curl-q ask stream health info terminate snapshot tail-logs run deploy lint typecheck test format

serve:
	@echo "Serving Modal app..."; \
	modal serve -m modal_backend.main

run:
	@echo "Running agent locally..."; \
	modal run -m modal_backend.main

deploy:
	@echo "Deploying to production..."; \
	modal deploy -m modal_backend.deploy

dev-url:
	@echo $(DEV_URL)

# Ask a question (POST /query with JSON object)
curl:
	@test -n "$(Q)" || { echo "Missing Q. Usage: make curl Q='Your question'"; exit 1; } ; \
	curl -X POST '$(DEV_URL)/query' \
	  -H 'Content-Type: application/json' $(CURL_PROXY_HEADERS) \
	  -d '{"question":"$(Q)"}'

# Stream a response (POST /query_stream)
stream:
	@test -n "$(Q)" || { echo "Missing Q. Usage: make stream Q='Your question'"; exit 1; } ; \
	curl -N -X POST '$(DEV_URL)/query_stream' \
	  -H 'Content-Type: application/json' $(CURL_PROXY_HEADERS) \
	  -d '{"question":"$(Q)"}'

health:
	curl -sS $(CURL_PROXY_HEADERS) '$(DEV_URL)/health'

info:
	curl -sS $(CURL_PROXY_HEADERS) '$(DEV_URL)/service_info'

terminate:
	modal run -m modal_backend.main::terminate_service_sandbox

snapshot:
	modal run -m modal_backend.main::snapshot_service

tail-logs:
	modal run -m modal_backend.main::tail_logs

# Development and testing
lint:
	@echo "Running linter..."; \
	ruff check modal_backend/ tests/

typecheck:
	@echo "Running type checker..."; \
	mypy modal_backend/ tests/

test:
	@echo "Running tests..."; \
	pytest tests/ -v

format:
	@echo "Formatting code..."; \
	ruff format modal_backend/ tests/

# Back-compat aliases
ask: curl
curl-q: curl

# Prevent Make from trying to execute stray words as targets
%:
	@:

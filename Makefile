# Makefile helpers for serving the Modal app and testing with curl.

# Usage:
#   Terminal 1: make serve
#   Terminal 2 (question input): make curl Q='What is 2+2?'

# Set this once to your dev endpoint URL (from `modal serve main.py`).
# Example: https://your-org--test-sandbox-http-app-dev.modal.run
DEV_URL ?= https://saidiibrahim--test-sandbox-http-app-dev.modal.run

.PHONY: serve dev-url curl curl-q ask stream health info terminate snapshot tail-logs

serve:
	@echo "Serving Modal app..."; \
	modal serve main.py

dev-url:
	@echo $(DEV_URL)

# Ask a question (POST /query with JSON object)
curl:
	@test -n "$(Q)" || { echo "Missing Q. Usage: make curl Q='Your question'"; exit 1; } ; \
	curl -X POST '$(DEV_URL)/query' \
	  -H 'Content-Type: application/json' \
	  -d '{"question":"$(Q)"}'

# Stream a response (POST /query_stream)
stream:
	@test -n "$(Q)" || { echo "Missing Q. Usage: make stream Q='Your question'"; exit 1; } ; \
	curl -N -X POST '$(DEV_URL)/query_stream' \
	  -H 'Content-Type: application/json' \
	  -d '{"question":"$(Q)"}'

health:
	curl -sS '$(DEV_URL)/health'

info:
	curl -sS '$(DEV_URL)/service_info'

terminate:
	modal run main.py::terminate_service_sandbox

snapshot:
	modal run main.py::snapshot_service

tail-logs:
	modal run main.py::tail_logs

# Back-compat aliases
ask: curl
curl-q: curl

# Prevent Make from trying to execute stray words as targets
%:
	@:
# Makefile helpers for serving the Modal app and testing with curl.

# Usage:
#   Terminal 1: make serve
#   Terminal 2 (question input): make curl Q='What is 2+2?'

# Set this once to your dev endpoint URL (from `modal serve main.py`).
# Example: https://your-org--test-sandbox-test-endpoint-dev.modal.run
DEV_URL ?= https://your-org--test-sandbox-test-endpoint-dev.modal.run

.PHONY: serve dev-url curl curl-q ask

serve:
	@echo "Serving Modal app..."; \
	modal serve main.py

dev-url:
	@echo $(DEV_URL)

curl:
	@test -n "$(Q)" || { echo "Missing Q. Usage: make curl Q='Your question'" ; exit 1; } ; \
	curl -X POST '$(DEV_URL)' \
	  -H 'Content-Type: application/json' \
	  -d '"$(Q)"'

# Back-compat aliases
ask: curl
curl-q: curl



.DEFAULT_GOAL := help

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
UV_VERSION ?= 0.8.13
TOOLS_DIR ?= $(ROOT)/.tools
UV_DIR ?= $(TOOLS_DIR)/uv-$(UV_VERSION)
UV ?= $(UV_DIR)/uv
UV_RUN := $(UV) run --project $(ROOT)

INPUT_DIR ?= inputs/proves
SERVER_HOST ?= 127.0.0.1
UART_BAUD ?= 115200
STATION_NAME ?= $(shell hostname)
GS_PORT ?= 5000

SPACECRAFT_ARG := $(if $(SPACECRAFT_ID),--spacecraft-id $(SPACECRAFT_ID),)
FRAME_LENGTH_ARG := $(if $(FRAME_LENGTH),--frame-length $(FRAME_LENGTH),)

.PHONY: help
help: ## Show available targets
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make <target>\n\n"} /^[a-zA-Z_0-9-]+:.*?##/ {printf "  %-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

$(TOOLS_DIR):
	mkdir -p $(TOOLS_DIR)

.PHONY: uv
uv: $(UV) ## Install the pinned UV executable

$(UV): | $(TOOLS_DIR)
	@test -s $(UV) || { mkdir -p $(UV_DIR); curl -LsSf https://astral.sh/uv/$(UV_VERSION)/install.sh | UV_UNMANAGED_INSTALL=$(UV_DIR) sh >/dev/null; }

.PHONY: venv
venv: uv ## Synchronize the client Python environment
	@$(UV) sync --project $(ROOT)

.PHONY: setup
setup: venv ## Install client dependencies

.PHONY: run
run: venv ## Run the serial ground-station client
	@test -n "$(UART_DEVICE)" || { echo "Error: set UART_DEVICE=/dev/ttyXXX" >&2; exit 1; }
	$(UV_RUN) proves-gs-client \
	  --mode serial \
	  --input-dir $(INPUT_DIR) \
	  --uart-device $(UART_DEVICE) \
	  --uart-baud $(UART_BAUD) \
	  --server-host $(SERVER_HOST) \
	  --station-name $(STATION_NAME) \
	  $(SPACECRAFT_ARG) $(FRAME_LENGTH_ARG)

.PHONY: run-tcp
run-tcp: venv ## Run the bent-pipe TCP ground-station client
	@test -n "$(GS_HOST)" || { echo "Error: set GS_HOST" >&2; exit 1; }
	$(UV_RUN) proves-gs-client \
	  --mode tcp \
	  --input-dir $(INPUT_DIR) \
	  --tcp-host $(GS_HOST) \
	  --tcp-port $(GS_PORT) \
	  --server-host $(SERVER_HOST) \
	  --station-name $(STATION_NAME) \
	  $(SPACECRAFT_ARG) $(FRAME_LENGTH_ARG)

.PHONY: test
test: venv ## Run client unit tests
	$(UV_RUN) pytest tests/unit

.PHONY: fmt
fmt: venv ## Check Python formatting and linting
	$(UV_RUN) ruff format --check src tests
	$(UV_RUN) ruff check src tests

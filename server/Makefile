.DEFAULT_GOAL := help

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
UV_VERSION ?= 0.8.13
TOOLS_DIR ?= $(ROOT)/.tools
UV_DIR ?= $(TOOLS_DIR)/uv-$(UV_VERSION)
UV ?= $(UV_DIR)/uv
UV_RUN := $(UV) run --project $(ROOT)
COMPOSE ?= $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")

INPUT_DIR ?= inputs/proves
RUNTIME_DIR ?= runtime
YAMCS_HOST ?= 127.0.0.1
YAMCS_URL ?= http://localhost:8090
YAMCS_INSTANCE ?= fprime-project
UART_BAUD ?= 115200
GS_PORT ?= 5000
YAMCS_UID ?= $(shell id -u)
YAMCS_GID ?= $(shell id -g)
export YAMCS_UID YAMCS_GID

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
venv: uv ## Synchronize the standalone Python environment
	@$(UV) sync --project $(ROOT) --frozen

.PHONY: patch-events
patch-events: venv
	@EVENT_PROCESSOR=$$(find $(ROOT)/.venv/lib -path '*/site-packages/fprime_yamcs/events/processor.py' -print -quit); \
	  test -n "$$EVENT_PROCESSOR" || { echo "fprime-yamcs event processor was not installed" >&2; exit 1; }; \
	  $(ROOT)/.venv/bin/python $(ROOT)/tools/apply_events_cpu_fix.py "$$EVENT_PROCESSOR"

.PHONY: docker-image
docker-image: ## Build the pinned Yamcs Docker image
	$(COMPOSE) build yamcs

.PHONY: setup
setup: patch-events docker-image ## Install Python dependencies and build Yamcs

.PHONY: prepare
prepare: patch-events ## Render configuration and generate the XTCE MDB
	@$(UV_RUN) proves-yamcs-prepare \
	  --input-dir $(INPUT_DIR) \
	  --runtime-dir $(RUNTIME_DIR) \
	  --template-dir config/etc

.PHONY: yamcs-config-check
yamcs-config-check: docker-image prepare ## Validate generated configuration with yamcsd
	$(COMPOSE) run --rm --no-deps yamcs \
	  --check --no-color --etc-dir /yamcs-config/etc \
	  --data-dir /yamcs-data --cache-dir /yamcs-cache

.PHONY: yamcs-dict yamcs-mdb
yamcs-dict: yamcs-config-check ## Validate the bundle and generate Yamcs configuration/MDB
yamcs-mdb: yamcs-dict ## Alias for yamcs-dict

.PHONY: yamcs-server
yamcs-server: yamcs-dict ## Start only the central Yamcs Docker server
	$(COMPOSE) up yamcs

.PHONY: yamcs
yamcs: yamcs-dict ## Start Yamcs, the event bridge, and the serial adapter
	@test -n "$(UART_DEVICE)" || { echo "Error: set UART_DEVICE=/dev/ttyXXX" >&2; exit 1; }
	$(UV_RUN) proves-yamcs-supervisor run \
	  --input-dir $(INPUT_DIR) \
	  --uart-device $(UART_DEVICE) \
	  --uart-baud $(UART_BAUD) \
	  --yamcs-host $(YAMCS_HOST) \
	  --yamcs-url $(YAMCS_URL) \
	  --instance $(YAMCS_INSTANCE) \
	  $(SPACECRAFT_ARG) $(FRAME_LENGTH_ARG)

.PHONY: yamcs-adapter-tcp
yamcs-adapter-tcp: patch-events ## Run the bent-pipe TCP adapter
	@test -n "$(GS_HOST)" || { echo "Error: set GS_HOST" >&2; exit 1; }
	$(UV_RUN) proves-yamcs-adapter \
	  --mode tcp \
	  --input-dir $(INPUT_DIR) \
	  --tcp-host $(GS_HOST) \
	  --tcp-port $(GS_PORT) \
	  --yamcs-host $(YAMCS_HOST) \
	  $(SPACECRAFT_ARG) $(FRAME_LENGTH_ARG)

.PHONY: yamcs-stop
yamcs-stop: venv ## Stop only this project's stack and owned processes
	@$(UV_RUN) proves-yamcs-supervisor stop

.PHONY: yamcs-build-check
yamcs-build-check: yamcs-dict ## Boot Yamcs, verify readiness, and tear it down
	$(UV_RUN) proves-yamcs-supervisor check \
	  --yamcs-url $(YAMCS_URL) \
	  --instance $(YAMCS_INSTANCE)

.PHONY: test
test: venv ## Run standalone unit tests
	$(UV_RUN) pytest tests/unit

.PHONY: test-yamcs
test-yamcs: venv ## Run the manual live-board Yamcs round-trip test
	YAMCS_URL=$(YAMCS_URL) YAMCS_INSTANCE=$(YAMCS_INSTANCE) \
	  $(UV_RUN) pytest tests/integration

.PHONY: fmt
fmt: venv ## Check Python formatting and linting
	$(UV_RUN) ruff format --check src tests tools
	$(UV_RUN) ruff check src tests tools

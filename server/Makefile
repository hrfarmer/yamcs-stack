.DEFAULT_GOAL := help

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
UV_VERSION ?= 0.8.13
TOOLS_DIR ?= $(ROOT)/.tools
UV_DIR ?= $(TOOLS_DIR)/uv-$(UV_VERSION)
UV ?= $(UV_DIR)/uv
UV_RUN := $(UV) run --project $(ROOT)
COMPOSE ?= $(shell \
	if docker compose version >/dev/null 2>&1; then echo "docker compose"; \
	elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; \
	else echo "docker compose"; fi)

INPUT_DIR ?= inputs/proves
RUNTIME_DIR ?= runtime
YAMCS_URL ?= http://localhost:8090
YAMCS_INSTANCE ?= fprime-project
YAMCS_UID ?= $(shell id -u)
YAMCS_GID ?= $(shell id -g)
export YAMCS_UID YAMCS_GID

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
venv: uv ## Synchronize the server Python environment
	@$(UV) sync --project $(ROOT) --frozen

.PHONY: patch-events
patch-events: venv
	@EVENT_PROCESSOR=$$(find $(ROOT)/.venv/lib -path '*/site-packages/fprime_yamcs/events/processor.py' -print -quit); \
	  test -n "$$EVENT_PROCESSOR" || { echo "fprime-yamcs event processor was not installed" >&2; exit 1; }; \
	  $(ROOT)/.venv/bin/python $(ROOT)/tools/apply_events_cpu_fix.py "$$EVENT_PROCESSOR"

.PHONY: ensure-docker
ensure-docker: ## Require a working Docker engine and Compose
	@command -v docker >/dev/null || { echo "docker is not installed" >&2; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "docker daemon is not running" >&2; exit 1; }
	@$(COMPOSE) version >/dev/null 2>&1 || { \
	  echo "Docker Compose is unavailable (tried: $(COMPOSE))" >&2; exit 1; }

.PHONY: docker-image
docker-image: ensure-docker ## Build the pinned Yamcs Docker image
	$(COMPOSE) build yamcs

.PHONY: setup-python
setup-python: patch-events ## Install Python deps and patch fprime-yamcs-events

.PHONY: setup
setup: setup-python docker-image ## Install Python dependencies and build Yamcs

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
yamcs: yamcs-dict ## Start Yamcs, the event bridge, and the multi-GS gateway
	$(UV_RUN) proves-yamcs-supervisor run \
	  --input-dir $(INPUT_DIR) \
	  --yamcs-url $(YAMCS_URL) \
	  --instance $(YAMCS_INSTANCE)

.PHONY: yamcs-stop
yamcs-stop: venv ## Stop only this project's stack and owned processes
	@$(UV_RUN) proves-yamcs-supervisor stop

.PHONY: yamcs-build-check
yamcs-build-check: yamcs-dict ## Boot Yamcs, verify readiness, and tear it down
	$(UV_RUN) proves-yamcs-supervisor check \
	  --yamcs-url $(YAMCS_URL) \
	  --instance $(YAMCS_INSTANCE)

.PHONY: test
test: venv ## Run server unit tests
	$(UV_RUN) pytest tests/unit

.PHONY: test-yamcs
test-yamcs: venv ## Run the manual live-board Yamcs round-trip test
	YAMCS_URL=$(YAMCS_URL) YAMCS_INSTANCE=$(YAMCS_INSTANCE) \
	  $(UV_RUN) pytest tests/integration

.PHONY: fmt
fmt: venv ## Check Python formatting and linting
	$(UV_RUN) ruff format --check src tests tools
	$(UV_RUN) ruff check src tests tools

.PHONY: lock
lock: uv ## Refresh uv.lock after dependency changes
	@$(UV) lock --project $(ROOT)

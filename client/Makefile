.DEFAULT_GOAL := help

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
UV_VERSION ?= 0.8.13
TOOLS_DIR ?= $(ROOT)/.tools
UV_DIR ?= $(TOOLS_DIR)/uv-$(UV_VERSION)
UV ?= $(UV_DIR)/uv
UV_RUN := $(UV) run --project $(ROOT)

# Path to a TOML client config (see config/*.example.toml).
CONFIG ?= config/gs.toml

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
run: venv ## Run the ground-station client using CONFIG=
	@test -f "$(CONFIG)" || { \
	  echo "Error: config file not found: $(CONFIG)" >&2; \
	  echo "Copy an example from config/ and edit it, e.g.:" >&2; \
	  echo "  cp config/gs.serial.example.toml config/gs.toml" >&2; \
	  exit 1; \
	}
	$(UV_RUN) proves-gs-client --config $(CONFIG)

.PHONY: test
test: venv ## Run client unit tests
	$(UV_RUN) pytest tests/unit

.PHONY: fmt
fmt: venv ## Check Python formatting and linting
	$(UV_RUN) ruff format --check src tests
	$(UV_RUN) ruff check src tests

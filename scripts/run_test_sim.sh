#!/usr/bin/env bash
# Long-running simulated stack for interactive testing without a PROVES board.
# Starts two F´ deployments plus Yamcs/Grafana; same as scripts/run_e2e_sim.sh --keep-alive.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/scripts/run_e2e_sim.sh" --keep-alive "$@"

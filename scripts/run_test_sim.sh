#!/usr/bin/env bash
# Long-running simulated stack for interactive testing without a PROVES board.
# Same process as scripts/run_e2e_sim.sh, but stays up until Ctrl+C.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/scripts/run_e2e_sim.sh" --keep-alive "$@"

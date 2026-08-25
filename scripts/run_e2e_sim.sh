#!/usr/bin/env bash
# Full-stack simulated e2e: build F´ reference, run F´ + Yamcs + gateway + GS client.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${E2E_WORK_DIR:-$ROOT/.e2e}"
FPRIME_REF_DIR="${FPRIME_REF_DIR:-$WORK/fprime-yamcs-reference}"
FPRIME_REF_URL="${FPRIME_REF_URL:-https://github.com/fprime-community/fprime-yamcs-reference.git}"
# Pin a release: main currently uses FPP "system" syntax that breaks with shallow/mismatched tools.
FPRIME_REF_REF="${FPRIME_REF_REF:-v0.1.0}"
BUNDLE_DIR="$WORK/bundle"
LOG_DIR="$WORK/logs"
FPRIME_UDP_PORT="${FPRIME_UDP_PORT:-52000}"
# Must not collide with the gateway's Yamcs-TC ingest port (also :50001) on localhost.
CLIENT_TC_PORT="${CLIENT_TC_PORT:-51001}"
BRIDGE_TCP_PORT="${BRIDGE_TCP_PORT:-5000}"
YAMCS_URL="${YAMCS_URL:-http://127.0.0.1:8090}"
YAMCS_INSTANCE="${YAMCS_INSTANCE:-fprime-project}"
GRAFANA_URL="${GRAFANA_URL:-http://127.0.0.1:3000}"
KEEP_ALIVE="${E2E_KEEP_ALIVE:-0}"
SKIP_TEST="${E2E_SKIP_TEST:-0}"

for arg in "$@"; do
  case "$arg" in
    --keep-alive) KEEP_ALIVE=1 ;;
    --skip-test) SKIP_TEST=1 ;;
    -h|--help)
      cat <<EOF
Usage: $(basename "$0") [--keep-alive] [--skip-test]

  --keep-alive   Leave F´, the GS client, Yamcs, Grafana, and the gateway
                 running until Ctrl+C (for interactive testing without a board).
  --skip-test    Start the stack without the CMD_NO_OP pytest round-trip.
EOF
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      echo "try: $(basename "$0") --help" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$WORK" "$LOG_DIR" "$BUNDLE_DIR"
PIDS=()
# Match the Makefile: Yamcs must run as the host user that owns runtime/{data,cache}.
# GitHub Actions runners are typically uid 1001; compose defaults to 1000 otherwise.
export YAMCS_UID="${YAMCS_UID:-$(id -u)}"
export YAMCS_GID="${YAMCS_GID:-$(id -g)}"

cleanup() {
  set +e
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  for pid in "${PIDS[@]:-}"; do
    wait "$pid" 2>/dev/null || true
  done
  (
    cd "$ROOT/server"
    docker compose ps >"$LOG_DIR/compose-ps.txt" 2>&1 || true
    docker compose logs --no-color >"$LOG_DIR/compose.log" 2>&1 || true
    docker compose down --remove-orphans
  ) >/dev/null 2>&1 || true
}
trap cleanup EXIT

log() { printf '[e2e] %s\n' "$*"; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

require_cmd git
require_cmd python3
require_cmd docker
docker compose version >/dev/null

if [[ ! -d "$FPRIME_REF_DIR/.git" ]]; then
  log "cloning $FPRIME_REF_URL ($FPRIME_REF_REF)"
  # Avoid `git clone --recursive --depth 1`: shallow recursive clones often miss the
  # pinned lib/fprime SHA and then FPP/tools disagree with the framework sources.
  git clone --branch "$FPRIME_REF_REF" --depth 1 "$FPRIME_REF_URL" "$FPRIME_REF_DIR"
  git -C "$FPRIME_REF_DIR" submodule update --init --recursive
else
  log "using existing F´ checkout at $FPRIME_REF_DIR"
fi

log "installing F´ Python tools into $WORK/fprime-venv"
python3 -m venv "$WORK/fprime-venv"
# shellcheck disable=SC1091
source "$WORK/fprime-venv/bin/activate"
python -m pip install -U pip wheel
python -m pip install -r "$FPRIME_REF_DIR/requirements.txt"
# Prefer the cmake package pinned by F´ over an older distro cmake when present.
export PATH="$WORK/fprime-venv/bin:$PATH"

DEPLOY="$FPRIME_REF_DIR/FprimeYamcsReference/YamcsDeployment"
log "building YamcsDeployment"
(
  cd "$DEPLOY"
  fprime-util generate
  fprime-util build
)

DICT_SRC="$(find "$FPRIME_REF_DIR/build-artifacts" -name '*Dictionary.json' -print -quit)"
BIN_SRC="$(find "$FPRIME_REF_DIR/build-artifacts" -type f \( -name 'YamcsDeployment' -o -name 'FprimeYamcsReference_YamcsDeployment' \) -path '*/bin/*' -print -quit)"
test -n "$DICT_SRC" && test -f "$DICT_SRC"
test -n "$BIN_SRC" && test -x "$BIN_SRC"
cp "$DICT_SRC" "$BUNDLE_DIR/fprime-dictionary.json"
FRAME_LENGTH="$(
  python3 - <<PY
import json
from pathlib import Path
dictionary = json.loads(Path("$BUNDLE_DIR/fprime-dictionary.json").read_text())
matches = [
    entry["value"]
    for entry in dictionary.get("constants", [])
    if entry.get("qualifiedName") == "ComCfg.TmFrameFixedSize"
]
assert len(matches) == 1, matches
print(int(matches[0], 0) if isinstance(matches[0], str) else int(matches[0]))
PY
)"
log "dictionary: $DICT_SRC"
log "binary: $BIN_SRC"
log "frame length: $FRAME_LENGTH"

log "preparing server + client environments"
(
  cd "$ROOT/server"
  make setup-python
  make docker-image
  make prepare INPUT_DIR="$BUNDLE_DIR"
)
(
  cd "$ROOT/client"
  make setup
)

log "starting Yamcs + Grafana (uid=$YAMCS_UID gid=$YAMCS_GID)"
(
  cd "$ROOT/server"
  # Fail fast on bad XTCE/config before the readiness wait.
  docker compose run --rm --no-deps yamcs \
    --check --no-color --etc-dir /yamcs-config/etc \
    --data-dir /yamcs-data --cache-dir /yamcs-cache
  docker compose up -d yamcs grafana
)
"$ROOT/server/.venv/bin/python" - <<PY
from proves_yamcs.supervisor import wait_until_grafana_ready, wait_until_ready
wait_until_ready("$YAMCS_URL", "$YAMCS_INSTANCE", 180)
wait_until_grafana_ready("$GRAFANA_URL", 180)
PY

log "starting event bridge"
"$ROOT/server/.venv/bin/fprime-yamcs-events" \
  --yamcs-url "$YAMCS_URL" \
  --instance "$YAMCS_INSTANCE" \
  --dictionary "$BUNDLE_DIR/fprime-dictionary.json" \
  >"$LOG_DIR/events.log" 2>&1 &
PIDS+=($!)

log "starting gateway"
"$ROOT/server/.venv/bin/proves-yamcs-gateway" \
  --yamcs-url "$YAMCS_URL" \
  --yamcs-instance "$YAMCS_INSTANCE" \
  >"$LOG_DIR/gateway.log" 2>&1 &
PIDS+=($!)

log "starting UDP/TCP bridge"
# TC frames are variable-length CCSDS; the bridge reassembles them from the
# TCP stream via the TC header length field (do not pass TM frame length).
python3 "$ROOT/sim/udp_tcp_bridge.py" \
  --tcp-port "$BRIDGE_TCP_PORT" \
  --fprime-tm-port "$FPRIME_UDP_PORT" \
  --fprime-tc-host 127.0.0.1 \
  --fprime-tc-port $((FPRIME_UDP_PORT + 1)) \
  >"$LOG_DIR/bridge.log" 2>&1 &
PIDS+=($!)

log "starting F´ YamcsDeployment"
"$BIN_SRC" -a 127.0.0.1 -p "$FPRIME_UDP_PORT" >"$LOG_DIR/fprime.log" 2>&1 &
PIDS+=($!)
sleep 2

log "starting GS client"
CLIENT_CONFIG="$WORK/gs-client.toml"
cat >"$CLIENT_CONFIG" <<EOF
mode = "tcp"
input_dir = "$BUNDLE_DIR"
server_host = "127.0.0.1"
station_name = "ci-gs"
skip_auth = true
tcp_host = "127.0.0.1"
tcp_port = $BRIDGE_TCP_PORT
tc_listen_port = $CLIENT_TC_PORT
tc_advertise_host = "127.0.0.1"
heartbeat_interval = 2.0
sequence_number_file = "$WORK/client-sequence"
EOF
"$ROOT/client/.venv/bin/proves-gs-client" \
  --config "$CLIENT_CONFIG" \
  >"$LOG_DIR/client.log" 2>&1 &
PIDS+=($!)

log "waiting for ground station registration"
"$ROOT/server/.venv/bin/python" - <<'PY'
import json, time, urllib.request
deadline = time.time() + 60
while time.time() < deadline:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8091/api/status", timeout=2) as response:
            status = json.load(response)
        online = [s for s in status.get("stations", []) if s.get("online")]
        if online:
            print(f"registered stations: {[s['name'] for s in online]}")
            break
    except Exception:
        pass
    time.sleep(1)
else:
    raise SystemExit("ground station did not register with gateway")
PY

if [[ "$SKIP_TEST" != "1" ]]; then
  log "running CMD_NO_OP round-trip test"
  set +e
  (
    cd "$ROOT/server"
    YAMCS_URL="$YAMCS_URL" YAMCS_INSTANCE="$YAMCS_INSTANCE" \
      .venv/bin/pytest tests/integration -q
  )
  test_status=$?
  set -e
  if [[ "$test_status" -ne 0 ]]; then
    if [[ "$KEEP_ALIVE" == "1" ]]; then
      log "round-trip test failed; leaving the stack up for inspection"
    else
      exit "$test_status"
    fi
  fi
else
  log "skipping CMD_NO_OP round-trip test"
fi

if [[ "$KEEP_ALIVE" == "1" ]]; then
  log "simulated stack is running (Ctrl+C to stop)"
  log "  Yamcs UI:    $YAMCS_URL"
  log "  Grafana:     $GRAFANA_URL  (PROVES Yamcs Overview)"
  log "  Gateway UI:  http://127.0.0.1:8091"
  log "  F´ TM/TC:    UDP :$FPRIME_UDP_PORT / :$((FPRIME_UDP_PORT + 1))"
  while true; do
    for pid in "${PIDS[@]}"; do
      if ! kill -0 "$pid" 2>/dev/null; then
        log "process $pid exited; shutting down"
        exit 1
      fi
    done
    sleep 2
  done
fi

log "e2e succeeded"

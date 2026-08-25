# PROVES Yamcs stack

Client/server ground system for PROVES flight software:

- [`server/`](server/): central Yamcs (Docker), F´ event bridge, and multi-ground-station gateway
- [`client/`](client/): ground-station radio passthrough (serial/TCP) over Tailscale to the gateway

```text
 radio board ──► GS client (HMAC + CCSDS) ──Tailscale──► gateway ──► Yamcs instances
                       ▲                                  │
                       └──────── TC (selected station) ───┘
```

Multiple F´ deployments (satellites) share one Yamcs process: each deployment
is a Yamcs instance with its own dictionary, spacecraft ID, and TM UDP port.
The list lives in `server/config/deployments.toml`. Multiple ground stations
may stream telemetry concurrently; the gateway routes frames by spacecraft ID
and deduplicates identical frames before Yamcs. Telecommands are sent through
the station selected via `/Ground/ActiveTxStation` in Yamcs or the gateway UI
on port 8091.

## Quick start

On the central host (Tailscale-reachable):

```sh
cd server
# place fprime-dictionary.json under inputs/proves/ (see config/deployments.toml)
make setup
make yamcs DEPLOYMENTS=tests/fixtures/deployments.toml
```

On each ground station:

```sh
cd client
# place matching dictionary + auth-key.hex under the satellite input_dir
cp config/gs.serial.example.toml config/gs.toml
# edit server_host, station_name, uart_device, and [[satellite]] tables
make run CONFIG=config/gs.toml
```

Open Yamcs at `http://<server>:8090` (instance selector chooses the satellite),
Grafana at `http://<server>:3000`, and the ground-station panel at
`http://<server>:8091`.

## Simulated full-stack CI / local e2e

```sh
./scripts/run_e2e_sim.sh
```

Builds [fprime-yamcs-reference](https://github.com/fprime-community/fprime-yamcs-reference)
(`v0.1.0` by default), runs F´ + UDP/TCP bridge + GS client + Yamcs gateway +
Grafana, and asserts a `CMD_NO_OP` round-trip. See [`sim/README.md`](sim/README.md).

To leave that same stack running for interactive testing (no PROVES board):

```sh
./scripts/run_test_sim.sh
```

Then open Yamcs (`:8090`, instance selector for `proves-flight` /
`proves-engineering`) and Grafana (`:3000`, one folder per deployment with
Overview and Commanding layouts). Ctrl+C stops the stack.

# PROVES Yamcs stack

Client/server ground system for PROVES flight software:

- [`server/`](server/): central Yamcs (Docker), F´ event bridge, and multi-ground-station gateway
- [`client/`](client/): ground-station radio passthrough (serial/TCP) over Tailscale to the gateway

```text
 radio board ──► GS client (HMAC + CCSDS) ──Tailscale──► gateway ──► Yamcs
                       ▲                                  │
                       └──────── TC (selected station) ───┘
```

Multiple ground stations may stream telemetry concurrently; the gateway
deduplicates identical frames before Yamcs. Telecommands are sent through the
station selected via `/Ground/ActiveTxStation` in Yamcs or the gateway UI on
port 8091.

## Quick start

On the central host (Tailscale-reachable):

```sh
cd server
# place fprime-dictionary.json under inputs/proves/
make setup
make yamcs
```

On each ground station:

```sh
cd client
# place matching dictionary + auth-key.hex under inputs/proves/
make setup
UART_DEVICE=/dev/ttyUSB0 SERVER_HOST=<yamcs-tailscale-name> STATION_NAME=gs-lab make run
```

Open Yamcs at `http://<server>:8090` and the ground-station panel at
`http://<server>:8091`.

## Simulated full-stack CI / local e2e

```sh
./scripts/run_e2e_sim.sh
```

Builds [fprime-yamcs-reference](https://github.com/fprime-community/fprime-yamcs-reference),
runs F´ + UDP/TCP bridge + GS client + Yamcs gateway, and asserts a `CMD_NO_OP`
round-trip. See [`sim/README.md`](sim/README.md).

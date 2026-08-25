# PROVES ground-station client

Runs on each ground station. Talks to the local radio board (UART or a TCP
bent-pipe) and passes CCSDS frames to the central Yamcs gateway over Tailscale.

## Responsibilities

- Scan/sync TM frames from serial (or read fixed frames from TCP)
- Send TM UDP datagrams to `SERVER_HOST:51000`
- Register with the gateway (`:8091`) so the station can be selected for TX
- Receive TC UDP on the local Tailscale address (`:50001` by default)
- Wrap telecommands with PROVES HMAC auth + space-data-link framing before the radio

## Setup

```sh
# Matching export from proves-core-reference (dictionary + auth-key.hex)
make setup
UART_DEVICE=/dev/ttyUSB0 \
  SERVER_HOST=<yamcs-tailscale-name-or-ip> \
  STATION_NAME=gs-lab \
  make run
```

TCP bent-pipe mode:

```sh
GS_HOST=radio-proxy.example SERVER_HOST=<yamcs-host> STATION_NAME=gs-tcp make run-tcp
```

Use `--tc-advertise-host` if auto-detection picks the wrong interface for TC
routing (it should be the station's Tailscale IPv4 address).

## Bundle

See [`inputs/proves/README.md`](inputs/proves/README.md). The auth key never
leaves the ground station.

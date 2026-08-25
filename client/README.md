# PROVES ground-station client

Runs on each ground station. Talks to the local radio board (UART or a TCP
bent-pipe) and passes CCSDS frames to the central Yamcs gateway over Tailscale.

## Responsibilities

- Scan/sync TM frames from serial for every configured satellite (or read
  fixed frames from TCP)
- Send TM UDP datagrams to the gateway TM ingest port (`:51000` by default)
- Register with the gateway (`:8091`) so the station can be selected for TX
- Receive TC UDP on the local Tailscale address (`:50001` by default)
- Wrap telecommands with the matching satellite's PROVES HMAC auth +
  space-data-link framing before the radio

Each `[[satellite]]` table points at a firmware bundle (`fprime-dictionary.json`
plus `auth-key.hex`). The client selects HMAC key and SCID from the incoming
Yamcs TC transfer frame.

A full first-time walkthrough (prerequisites, Tailscale, matching the
server dictionary) is in **[SETUP.md](../SETUP.md)**.

## Setup

```sh
make setup
cp config/gs.serial.example.toml config/gs.toml
# edit config/gs.toml: server_host, station_name, uart_device, [[satellite]]
make run CONFIG=config/gs.toml
```

TCP bent-pipe mode:

```sh
cp config/gs.tcp.example.toml config/gs.toml
# edit tcp_host / server_host / station_name / [[satellite]]
make run CONFIG=config/gs.toml
```

Or invoke the client directly:

```sh
.venv/bin/proves-gs-client --config config/gs.toml
```

Station-level CLI flags still override individual keys for one-off debugging;
prefer editing the TOML file for normal operation. Set `tc_advertise_host` if
auto-detection picks the wrong interface for TC routing (use the station's
Tailscale IPv4 address). TCP mode requires every listed satellite to share a
TM frame length.

## Bundle

See [`inputs/proves/README.md`](inputs/proves/README.md). The auth key never
leaves the ground station.

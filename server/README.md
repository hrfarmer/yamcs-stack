# PROVES Yamcs server

This directory owns the central Yamcs server, the F Prime event bridge, and the
PROVES serial/TCP authentication adapter. It consumes a two-file bundle exported
by `proves-core-reference`; it does not read files from a firmware checkout.

## First run

Build and flash the firmware, then export its matching ground configuration:

```sh
cd ~/code/spacelab/proves-core-reference
make build
make yamcs-export

cd ../yamcs-stack/server
make setup
UART_DEVICE=/dev/ttyXXX make yamcs
```

The web interface is available at <http://localhost:8090>. Stop the complete
local stack with `make yamcs-stop`.

`make yamcs` runs the Yamcs container, event bridge, and serial adapter together.
`make yamcs-server` runs only the central server. For a TCP bent-pipe connection:

```sh
make yamcs-adapter-tcp GS_HOST=ground-station.example GS_PORT=5000 YAMCS_HOST=127.0.0.1
```

Run `make help` for all supported targets and `make test` for unit tests.

## Runtime data and security

The real dictionary, HMAC key, generated configuration, archives, cache, logs,
and process state are ignored by Git. The server generates a persistent Yamcs
token-signing secret under `runtime/secrets` on first preparation.

This initial extraction intentionally preserves the development configuration:
the API binds all interfaces, CORS is open, and Yamcs operator authentication and
TLS are not configured. Do not expose it directly to an untrusted network.

## Ports

- TCP 8090: Yamcs HTTP/WebSocket API and web interface.
- UDP 50000: telemetry sent by the host adapter into the Yamcs container.
- UDP 50001: telecommands sent from the container to the host adapter. This is
  not published by Compose because the adapter must bind that host port.


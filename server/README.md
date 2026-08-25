# PROVES Yamcs server

Central Yamcs instance, F Prime event bridge, and multi-ground-station gateway.
Radio hardware stays on the [`../client`](../client) package.

## Architecture

```text
GS clients (Tailscale)          This host
  TM UDP :51000  ─────────────► gateway ──UDP──► Yamcs :50000 (loopback)
  TC UDP :50001  ◄───────────── gateway ◄─UDP── Yamcs :50001 (host.docker.internal)
  HTTP heartbeat ─────────────► gateway :8091
Yamcs UI / API                  :8090
```

The gateway:

- accepts TM from every registered station and **deduplicates** identical frames
  within a short window before forwarding into Yamcs
- forwards TC from Yamcs to the **active TX station**
- exposes a small UI/API on `:8091` and mirrors selection to the Yamcs local
  parameter `/Ground/ActiveTxStation`

## First run

```sh
# Dictionary export from proves-core-reference (auth key optional on the server)
cd ~/code/spacelab/proves-core-reference && make yamcs-export

cd ../yamcs-stack/server
make setup
make yamcs
```

Web UI: <http://localhost:8090>  
Ground stations: <http://localhost:8091>

Stop with `make yamcs-stop`. `make yamcs-server` starts only the Yamcs container.

## Selecting a transmit station

1. Ensure each GS client is heartbeating (online in the gateway UI).
2. Set `/Ground/ActiveTxStation` in Yamcs (Parameters → set value), **or**
3. Choose the station in the gateway UI / `PUT /api/active-tx`.

## Ports

| Port | Role |
|------|------|
| TCP 8090 | Yamcs HTTP / web |
| TCP 8091 | Gateway API + GS UI |
| UDP 51000 | TM ingest from GS clients (Tailscale) |
| UDP 50000 | Yamcs TM in (loopback only; fed by gateway) |
| UDP 50001 | Yamcs TC out → gateway (then to selected GS) |

## Security

Development posture: API binds all interfaces, CORS is open, no Yamcs operator
auth/TLS. Rely on Tailscale for network access control; do not expose these
ports on the public Internet.

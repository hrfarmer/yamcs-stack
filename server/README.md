# PROVES Yamcs server

Central Yamcs process (one instance per F´ deployment), F Prime event bridge,
and multi-ground-station gateway. Radio hardware stays on the
[`../client`](../client) package.

## Architecture

```text
GS clients (Tailscale)          This host
  TM UDP :51000  ─────────────► gateway ──UDP──► Yamcs TM ports (loopback, per SCID)
  TC UDP :50001  ◄───────────── gateway ◄─UDP── Yamcs :50001 (host.docker.internal)
  HTTP heartbeat ─────────────► gateway :8091
Yamcs UI / API                  :8090  (switch instance to pick a satellite)
Grafana (JAOPS Yamcs plugin)    :3000
```

Deployments are declared in [`config/deployments.toml`](config/deployments.toml).
The gateway:

- accepts TM from every registered station, **routes by CCSDS spacecraft ID**
  to the matching Yamcs instance, and **deduplicates** identical frames
- forwards TC from every instance to the **active TX station**
- exposes a small UI/API on `:8091` and mirrors selection to the Yamcs local
  parameter `/Ground/ActiveTxStation` on every instance

## First run

```sh
# Dictionary export from proves-core-reference (auth key optional on the server)
cd ~/code/spacelab/proves-core-reference && make yamcs-export

cd ../yamcs-stack/server
# point config/deployments.toml input_dir at the exported dictionary
make setup
make yamcs
```

CI and local fixtures use `tests/fixtures/deployments.toml`:

```sh
make yamcs DEPLOYMENTS=tests/fixtures/deployments.toml
```

Web UI: <http://localhost:8090>  
Grafana: <http://localhost:3000> (JAOPS Yamcs plugin; anonymous Admin)  
Ground stations: <http://localhost:8091>

Stop with `make yamcs-stop`. `make yamcs-server` starts Yamcs and Grafana.

## Selecting a transmit station

1. Ensure each GS client is heartbeating (online in the gateway UI).
2. Set `/Ground/ActiveTxStation` in Yamcs (Parameters → set value), **or**
3. Choose the station in the gateway UI / `PUT /api/active-tx`.

Pick the **satellite** with the Yamcs instance selector (one instance per
`[[deployment]]`).

## Ports

| Port | Role |
|------|------|
| TCP 8090 | Yamcs HTTP / web |
| TCP 3000 | Grafana (JAOPS Yamcs app + datasource) |
| TCP 8091 | Gateway API + GS UI |
| UDP 51000 | TM ingest from GS clients (Tailscale) |
| UDP 50000, 50002, … | Yamcs TM in (loopback; one port per deployment) |
| UDP 50001 | Yamcs TC out → gateway (then to selected GS) |

## Security

Development posture: API binds all interfaces, CORS is open, no Yamcs operator
auth/TLS, and Grafana allows anonymous Admin. Rely on Tailscale for network
access control; do not expose these ports on the public Internet.

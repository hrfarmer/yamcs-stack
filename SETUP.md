# Setup

Linux walkthrough to boot the **central Yamcs server** and attach **ground
stations**. Run server commands from `server/`, client commands from
`client/`. Use the `make` targets (they install Python for you).

```text
 radio board ──► GS client ──Tailscale──► gateway ──► Yamcs instances (:8090)
                       ▲                    │ (:8091)
                       └──── TC (one selected station) ────┘
                                              Grafana (:3000)
```

Each F´ deployment is one Yamcs instance (own dictionary / spacecraft ID)
listed in `server/config/deployments.toml`. You need **one always-on server**
(Docker) and **one host per radio**. The same machine can do both for a
local test. Join them with [Tailscale](https://tailscale.com/) and keep
ports off the public Internet (Yamcs and Grafana have no login).

## Before you start

**Server:** `git`, `curl`, Docker Engine + Compose (`docker info` works).
First `make setup` builds the Yamcs image and can take a while.

**Each ground station:** `git`, `curl`. For UART, you need the serial
device (often `/dev/ttyUSB0`) and `dialout` on Debian/Ubuntu:
`sudo usermod -aG dialout "$USER"` then log in again. No Docker.

**Files from flight software** (same dictionary on server and client;
never commit the key):

```sh
cd ~/code/spacelab/proves-core-reference   # or your FSW checkout
make yamcs-export
```

| File | Server | Ground station |
| --- | --- | --- |
| `fprime-dictionary.json` | required per deployment | required per `[[satellite]]` |
| `auth-key.hex` | optional | required for a real radio |

Clone this repo on every machine first.

---

## 1. Server

Default `config/deployments.toml` hosts `proves-flight` from
`inputs/proves/`:

```sh
cd server
make setup

mkdir -p inputs/proves
cp /path/to/export/fprime-dictionary.json inputs/proves/

make yamcs
```

That last command stays in the foreground. Stop it with `make yamcs-stop`
from another terminal.

- Yamcs: http://localhost:8090 (instance selector picks the satellite)
- Grafana: http://localhost:3000 (one folder per deployment)
- Ground stations: http://localhost:8091 (empty until a client connects)

No flight export yet? Use the test deployments file:

```sh
make yamcs DEPLOYMENTS=tests/fixtures/deployments.toml
```

A second satellite is another `[[deployment]]` in `config/deployments.toml`
plus its own `input_dir` (and dictionary). TM ports default to
`50000, 50002, …`; TC from every instance shares UDP `50001`.

---

## 2. Each ground station

```sh
cd client
make setup

mkdir -p inputs/proves
cp /path/to/export/fprime-dictionary.json inputs/proves/
cp /path/to/export/auth-key.hex inputs/proves/

cp config/gs.serial.example.toml config/gs.toml   # or gs.tcp.example.toml
```

Edit `config/gs.toml`:

```toml
server_host = "yamcs-server"   # Tailscale name or 100.x IPv4
station_name = "gs-lab"        # unique per station
uart_device = "/dev/ttyUSB0"   # serial mode; TCP mode uses tcp_host / tcp_port

[[satellite]]
name = "proves-flight"
input_dir = "inputs/proves"
```

Add another `[[satellite]]` for each extra deployment (matching
`deployments.toml`). If telecommands never arrive, set
`tc_advertise_host` to this station’s Tailscale IPv4.

```sh
make run
```

Leave it running. For a TCP bent-pipe (no UART), copy
`config/gs.tcp.example.toml` instead and set `tcp_host` / `tcp_port`.
`skip_auth = true` on a `[[satellite]]` is for simulators only, not real
radios.

---

## 3. Send traffic

1. Open `http://<server>:8091` — the station should be **online**.
2. Select it as TX and click **Apply** (or set `/Ground/ActiveTxStation` in
   Yamcs).
3. Open Yamcs at `:8090` and pick the instance. TM should move once the
   radio is producing frames. `CMD_NO_OP` is a safe command check.
   Grafana at `:3000` has Overview / Commanding per deployment.

No TX station selected → gateway shows `tc_dropped`. That is normal.

---

## If it fails

| What you see | Try |
| --- | --- |
| Docker / daemon errors | `docker info` |
| `dictionary is missing` | copy the JSON into the deployment `input_dir` |
| Heartbeat failed / station missing | `curl http://<server>:8091/api/status` from the GS host |
| Station online, TC dropped | select TX; set `tc_advertise_host` |
| Serial open fails | device path, baud, `dialout` |
| Client wants a key | add `auth-key.hex`, or `skip_auth` only for a sim |

Ports on the server: TCP `8090` (Yamcs), TCP `3000` (Grafana), TCP `8091`
(gateway), UDP `51000` (TM). On each GS: UDP `50001` (TC). Tailscale only.

No radio? Keep a two-satellite sim up with `./scripts/run_test_sim.sh`
(see [`sim/README.md`](sim/README.md)). More detail:
[`server/README.md`](server/README.md), [`client/README.md`](client/README.md).

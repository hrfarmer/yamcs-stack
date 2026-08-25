# Setup

Linux walkthrough to boot the **central Yamcs server** and attach **ground
stations**. Run server commands from `server/`, client commands from
`client/`. Use the `make` targets (they install Python for you).

```text
 radio board ──► GS client ──Tailscale──► gateway ──► Yamcs (:8090)
                       ▲                    │ (:8091)
                       └──── TC (one selected station) ────┘
```

You need **one always-on server** (Docker) and **one host per radio**. The
same machine can do both for a local test. Join them with
[Tailscale](https://tailscale.com/) and keep ports off the public Internet
(Yamcs has no login).

## Before you start

**Server:** `git`, `curl`, Docker Engine + Compose (`docker info` works).
First `make setup` builds the Yamcs image and can take a while.

**Each ground station:** `git`, `curl`. For UART, you need the serial
device (often `/dev/ttyUSB0`) and `dialout` on Debian/Ubuntu:
`sudo usermod -aG dialout "$USER"` then log in again. No Docker.

**Files from flight software** (same dictionary on every machine; never
commit the key):

```sh
cd ~/code/spacelab/proves-core-reference   # or your FSW checkout
make yamcs-export
```

| File | Server | Ground station |
| --- | --- | --- |
| `fprime-dictionary.json` | required | required |
| `auth-key.hex` | optional | required for a real radio |

Clone this repo on every machine first.

---

## 1. Server

```sh
cd server
make setup

mkdir -p inputs/proves
cp /path/to/export/fprime-dictionary.json inputs/proves/

make yamcs
```

That last command stays in the foreground. Stop it with `make yamcs-stop`
from another terminal.

- Yamcs: http://localhost:8090 (or `http://<tailscale-name>:8090`)
- Ground stations: http://localhost:8091 (empty until a client connects)

No flight export yet? Use the test dictionary instead:

```sh
make yamcs INPUT_DIR=tests/fixtures/proves
```

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
```

If telecommands never arrive, set `tc_advertise_host` to this station’s
Tailscale IPv4.

```sh
make run
```

Leave it running. For a TCP bent-pipe (no UART), copy
`config/gs.tcp.example.toml` instead and set `tcp_host` / `tcp_port`.
`skip_auth = true` is for simulators only, not real radios.

---

## 3. Send traffic

1. Open `http://<server>:8091` — the station should be **online**.
2. Select it as TX and click **Apply** (or set `/Ground/ActiveTxStation` in
   Yamcs).
3. Open Yamcs at `:8090`, instance `fprime-project`. TM should move once
   the radio is producing frames. `CMD_NO_OP` is a safe command check.

No TX station selected → gateway shows `tc_dropped`. That is normal.

---

## If it fails

| What you see | Try |
| --- | --- |
| Docker / daemon errors | `docker info` |
| `dictionary is missing` | copy the JSON into `inputs/proves/` |
| Heartbeat failed / station missing | `curl http://<server>:8091/api/status` from the GS host |
| Station online, TC dropped | select TX; set `tc_advertise_host` |
| Serial open fails | device path, baud, `dialout` |
| Client wants a key | add `auth-key.hex`, or `skip_auth` only for a sim |

Ports on the server: TCP `8090` (Yamcs), TCP `8091` (gateway), UDP `51000`
(TM). On each GS: UDP `50001` (TC). Tailscale only.

Full-stack sim without a radio: [`sim/README.md`](sim/README.md)
(`./scripts/run_e2e_sim.sh`). More detail:
[`server/README.md`](server/README.md), [`client/README.md`](client/README.md).
